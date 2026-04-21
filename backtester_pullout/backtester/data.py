"""Swap event streamer — vectorized loads via raw asyncpg.

Usage:
    async for df in stream_swaps(pool_addr, start_block, end_block, chunk_blocks=50_000):
        # df has columns: block, time, tick, sqrt_price_x96, amount0, amount1, liquidity
        # all integer columns are python ints (exact, unbounded).

Or load the whole range into one frame:
    df = await load_swaps(pool_addr, start_block, end_block)

Address normalization: the indexer stores pool addresses lowercase without
the 0x prefix. `normalize_addr` matches that.
"""
from __future__ import annotations

import os
from typing import AsyncIterator

import asyncpg
import pandas as pd
from dotenv import load_dotenv


SWAP_COLUMNS = [
    "evt_block_number",
    "evt_block_time",
    "tick",
    "sqrt_price_x96",
    "amount0",
    "amount1",
    "liquidity",
]

SWAP_QUERY = f"""
    SELECT {", ".join(SWAP_COLUMNS)}
    FROM base_poocl_swaps_v2
    WHERE evt_address = $1
      AND evt_block_number >= $2
      AND evt_block_number <= $3
    ORDER BY evt_block_number ASC, id ASC
"""


# Mints + burns share the same column shape (tick_lower, tick_upper, amount = L)
# so we can union them. amount is signed in our combined view: + for mints, - for burns.
LIQ_EVENT_COLUMNS = ["evt_block_number", "tick_lower", "tick_upper", "amount", "kind"]

LIQ_EVENT_QUERY = """
    SELECT evt_block_number, tick_lower, tick_upper, amount, 1 AS kind
    FROM base_poocl_mints_v2
    WHERE evt_address = $1
      AND evt_block_number >= $2
      AND evt_block_number <= $3
    UNION ALL
    SELECT evt_block_number, tick_lower, tick_upper, amount, -1 AS kind
    FROM base_univ3_burns_v2
    WHERE evt_address = $1
      AND evt_block_number >= $2
      AND evt_block_number <= $3
    ORDER BY 1 ASC
"""

LIQ_EVENT_QUERY_UPTO = """
    SELECT evt_block_number, tick_lower, tick_upper, amount, 1 AS kind
    FROM base_poocl_mints_v2
    WHERE evt_address = $1
      AND evt_block_number <= $2
    UNION ALL
    SELECT evt_block_number, tick_lower, tick_upper, amount, -1 AS kind
    FROM base_univ3_burns_v2
    WHERE evt_address = $1
      AND evt_block_number <= $2
    ORDER BY 1 ASC
"""


def normalize_addr(addr: str) -> str:
    return addr.lower().removeprefix("0x")


def _resolve_dsn() -> str:
    load_dotenv()
    url = os.environ.get("BACKTESTER_DB_URL")
    if url:
        # asyncpg wants postgres:// or postgresql://, not postgresql+asyncpg://
        return url.replace("postgresql+asyncpg://", "postgresql://", 1)
    # fall back to component env vars
    host = os.environ["JOBS_POSTGRES_HOST"]
    port = os.environ.get("JOBS_POSTGRES_PORT", "5432")
    user = os.environ["JOBS_POSTGRES_USER"]
    pw = os.environ["JOBS_POSTGRES_PASSWORD"]
    db = os.environ["JOBS_POSTGRES_DB"]
    return f"postgresql://{user}:{pw}@{host}:{port}/{db}"


def _rows_to_df(rows: list[asyncpg.Record]) -> pd.DataFrame:
    """Convert asyncpg rows to a DataFrame with python-int columns.

    DecimalField in Postgres comes back as `Decimal`; we cast to `int` since
    all three (sqrt_price_x96, amounts, liquidity) are whole-number uint/int256.
    Keep them as Python ints via object dtype so they never lose precision.
    """
    if not rows:
        return pd.DataFrame({c: [] for c in SWAP_COLUMNS}).rename(
            columns={"evt_block_number": "block", "evt_block_time": "time"}
        )

    df = pd.DataFrame(rows, columns=SWAP_COLUMNS)
    df = df.rename(columns={"evt_block_number": "block", "evt_block_time": "time"})

    # Cast Decimal → int (object dtype preserves arbitrary precision).
    for col in ("sqrt_price_x96", "amount0", "amount1", "liquidity"):
        df[col] = df[col].map(lambda x: int(x) if x is not None else 0).astype(object)

    df["block"] = df["block"].astype("int64")
    df["tick"] = df["tick"].astype("int64")
    # time is nullable and in some indexers stored as text — coerce
    df["time"] = pd.to_numeric(df["time"], errors="coerce").astype("Int64")
    return df


async def load_swaps(
    pool_addr: str,
    start_block: int,
    end_block: int,
    *,
    dsn: str | None = None,
) -> pd.DataFrame:
    """Load all swaps for a pool in [start_block, end_block] into one DataFrame."""
    dsn = dsn or _resolve_dsn()
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(SWAP_QUERY, normalize_addr(pool_addr), start_block, end_block)
        return _rows_to_df(rows)
    finally:
        await conn.close()


def _liq_rows_to_df(rows) -> pd.DataFrame:
    """Mint/burn rows → DataFrame. amount stays as Python int (object dtype),
    kind is +1 for mint, -1 for burn."""
    if not rows:
        return pd.DataFrame({c: [] for c in LIQ_EVENT_COLUMNS})
    df = pd.DataFrame(rows, columns=LIQ_EVENT_COLUMNS)
    df["evt_block_number"] = df["evt_block_number"].astype("int64")
    df["tick_lower"] = df["tick_lower"].astype("int64")
    df["tick_upper"] = df["tick_upper"].astype("int64")
    df["kind"] = df["kind"].astype("int8")
    df["amount"] = df["amount"].map(lambda x: int(x) if x is not None else 0).astype(object)
    return df.rename(columns={"evt_block_number": "block"})


async def load_liq_events(
    pool_addr: str,
    start_block: int,
    end_block: int,
    *,
    dsn: str | None = None,
) -> pd.DataFrame:
    """Mint+burn events for a pool in [start, end] block-sorted."""
    dsn = dsn or _resolve_dsn()
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(LIQ_EVENT_QUERY, normalize_addr(pool_addr), start_block, end_block)
        return _liq_rows_to_df(rows)
    finally:
        await conn.close()


async def load_liq_events_upto(
    pool_addr: str,
    upto_block: int,
    *,
    dsn: str | None = None,
) -> pd.DataFrame:
    """All mint+burn events for a pool from inception through `upto_block`.

    Used to seed the LiquidityMap before a backtest start_block.
    """
    dsn = dsn or _resolve_dsn()
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(LIQ_EVENT_QUERY_UPTO, normalize_addr(pool_addr), upto_block)
        return _liq_rows_to_df(rows)
    finally:
        await conn.close()


async def stream_swaps(
    pool_addr: str,
    start_block: int,
    end_block: int,
    *,
    chunk_blocks: int = 50_000,
    dsn: str | None = None,
) -> AsyncIterator[pd.DataFrame]:
    """Yield swaps in block-range chunks.

    Chunks are half-open on the upper side only at the range boundary —
    within the loop each yielded frame covers [lo, hi] inclusive. This keeps
    the engine's per-chunk logic simple: a block can only appear once.
    """
    dsn = dsn or _resolve_dsn()
    conn = await asyncpg.connect(dsn)
    try:
        addr = normalize_addr(pool_addr)
        lo = start_block
        while lo <= end_block:
            hi = min(lo + chunk_blocks - 1, end_block)
            rows = await conn.fetch(SWAP_QUERY, addr, lo, hi)
            yield _rows_to_df(rows)
            lo = hi + 1
    finally:
        await conn.close()

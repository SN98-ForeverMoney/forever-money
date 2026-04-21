"""Integration tests for the swap streamer.

These require BACKTESTER_DB_URL env var set to a reachable Postgres.
Marked `integration` — skip by default, run with `-m integration`.
"""
import os

import pytest

from backtester_pullout.backtester.data import (
    SWAP_COLUMNS,
    load_swaps,
    normalize_addr,
    stream_swaps,
)


pytestmark = pytest.mark.integration

POOL = "0xb2cc224c1c9feE385f8ad6a55b4d94E92359DC59"  # WETH/USDC on Base
START = 40_000_000
END = 40_001_000  # 1k blocks — small


def _have_db() -> bool:
    return bool(os.environ.get("BACKTESTER_DB_URL")) or bool(
        os.environ.get("JOBS_POSTGRES_HOST")
    )


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _have_db(), reason="no DB URL in env"),
]


def test_normalize_addr():
    assert normalize_addr("0xABCDef") == "abcdef"
    assert normalize_addr("abcdef") == "abcdef"


async def test_load_swaps_small_range():
    df = await load_swaps(POOL, START, END)
    assert len(df) > 0, "expected swaps in this range"
    # Columns present and renamed
    assert "block" in df.columns
    assert "time" in df.columns
    assert {"tick", "sqrt_price_x96", "amount0", "amount1", "liquidity"} <= set(df.columns)
    # Sorted ascending by block
    assert (df["block"].values[1:] >= df["block"].values[:-1]).all()
    # Block bounds
    assert df["block"].min() >= START
    assert df["block"].max() <= END
    # Big-int columns are python ints (object dtype)
    assert isinstance(df["sqrt_price_x96"].iloc[0], int)
    assert isinstance(df["liquidity"].iloc[0], int)
    # sqrt_price_x96 is uint160, always > 0
    assert df["sqrt_price_x96"].iloc[0] > 0


async def test_stream_matches_load():
    # Streaming in chunks should produce the same rows as one-shot load.
    full = await load_swaps(POOL, START, END)
    streamed_rows = 0
    async for chunk in stream_swaps(POOL, START, END, chunk_blocks=200):
        streamed_rows += len(chunk)
    assert streamed_rows == len(full)


async def test_empty_range_returns_empty_df():
    # Use a clearly empty block range
    df = await load_swaps(POOL, 1, 100)
    assert len(df) == 0
    assert set(df.columns) >= {"block", "time", "tick", "sqrt_price_x96"}

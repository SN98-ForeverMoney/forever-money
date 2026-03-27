"""
Shared infrastructure for analysis scripts.

Contains common utilities for RPC/Blockscout data fetching, event processing,
timeline building, caching, and serialization used by both vault_analysis.py
and chutes_analysis.py.
"""

import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import requests
from web3 import Web3

# ---------------------------------------------------------------------------
# Project paths & imports
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

ANALYSIS_DIR = Path(__file__).resolve().parent
CACHE_DIR = ANALYSIS_DIR / "data"
OUTPUT_DIR = ANALYSIS_DIR / "output"
CACHE_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

ABI_DIR = PROJECT_ROOT / "validator" / "utils" / "abis"


BLOCKSCOUT_BASE = "https://base.blockscout.com/api/v2"

# ---------------------------------------------------------------------------
# Web3 instance (shared)
# ---------------------------------------------------------------------------
BASE_RPC = os.environ.get("BASE_RPC", "https://base-mainnet.public.blastapi.io")
w3 = Web3(Web3.HTTPProvider(BASE_RPC, request_kwargs={"timeout": 30}))


# ---------------------------------------------------------------------------
# ABI loading
# ---------------------------------------------------------------------------

def load_abi(name: str) -> list:
    with open(ABI_DIR / f"{name}.json") as f:
        data = json.load(f)
    return data["abi"] if isinstance(data, dict) and "abi" in data else data


POOL_ABI = load_abi("ICLPool")
ERC20_ABI = load_abi("ERC20")


# ---------------------------------------------------------------------------
# Block helpers
# ---------------------------------------------------------------------------

def get_block_timestamp(block_num: int) -> int:
    block = w3.eth.get_block(block_num)
    return block["timestamp"]


# ---------------------------------------------------------------------------
# Blockscout: get tx hashes per vault
# ---------------------------------------------------------------------------

def _fetch_blockscout_paginated(url: str, tx_hash_key: str = "transaction_hash") -> set[str]:
    """Fetch paginated results from Blockscout, extracting tx hashes."""
    tx_hashes = set()
    page = 0
    while url and page < 50:
        for attempt in range(3):
            try:
                resp = requests.get(url, timeout=15)
                resp.raise_for_status()
                break
            except Exception as e:
                if attempt < 2:
                    time.sleep(1)
                else:
                    print(f"    Failed after 3 attempts: {e}")
                    return tx_hashes

        data = resp.json()
        items = data.get("items", [])
        for item in items:
            tx = item.get(tx_hash_key, "") or item.get("hash", "")
            if tx:
                tx_hashes.add(tx.lower())

        next_params = data.get("next_page_params")
        if next_params and items:
            base_url = url.split("?")[0]
            params = "&".join(f"{k}={v}" for k, v in next_params.items())
            url = f"{base_url}?{params}"
            page += 1
        else:
            url = None
    return tx_hashes


def get_vault_tx_hashes(vault_address: str) -> set[str]:
    """
    Get all unique transaction hashes involving a vault address via
    BOTH Blockscout token-transfers AND transactions endpoints.
    """
    addr = vault_address.lower()
    tx_hashes = set()

    tx_hashes |= _fetch_blockscout_paginated(
        f"{BLOCKSCOUT_BASE}/addresses/{addr}/token-transfers",
        tx_hash_key="transaction_hash",
    )

    tx_hashes |= _fetch_blockscout_paginated(
        f"{BLOCKSCOUT_BASE}/addresses/{addr}/transactions",
        tx_hash_key="hash",
    )

    return tx_hashes


# ---------------------------------------------------------------------------
# Event fetching from RPC
# ---------------------------------------------------------------------------

def fetch_logs_chunked(
    pool_address: str,
    topics: list,
    from_block: int,
    to_block: int,
    chunk_size: int = 10_000,
) -> list:
    """Fetch eth_getLogs in chunks with retry logic."""
    all_logs = []
    current = from_block
    total_chunks = (to_block - from_block) // chunk_size + 1
    chunk_idx = 0

    while current <= to_block:
        end = min(current + chunk_size - 1, to_block)
        chunk_idx += 1
        for attempt in range(5):
            try:
                logs = w3.eth.get_logs({
                    "address": Web3.to_checksum_address(pool_address),
                    "topics": topics,
                    "fromBlock": current,
                    "toBlock": end,
                })
                all_logs.extend(logs)
                break
            except Exception as e:
                if attempt < 4:
                    wait = 2 ** attempt
                    print(f"    Retry {attempt+1} (chunk {chunk_idx}/{total_chunks}): {e}")
                    time.sleep(wait)
                else:
                    print(f"    FAILED chunk {current}-{end}: {e}")
        if chunk_idx % 20 == 0:
            print(f"    Progress: {chunk_idx}/{total_chunks} chunks, {len(all_logs)} logs so far")
        current = end + 1

    return all_logs


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def _serialize(v: Any) -> Any:
    if isinstance(v, bytes):
        return "0x" + v.hex()
    if isinstance(v, int) and abs(v) > 2**53:
        return str(v)
    return v


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def save_cache(cache_file: Path, data: dict):
    """Save data dict to JSON cache file."""
    with open(cache_file, "w") as f:
        json.dump(data, f, indent=2)
    total = sum(len(v) for v in data.get("events", {}).values())
    print(f"Cached {total} events to {cache_file}")


def load_cache(cache_file: Path) -> dict | None:
    """Load JSON cache file, or return None if missing."""
    if not cache_file.exists():
        return None
    with open(cache_file) as f:
        data = json.load(f)
    total = sum(len(v) for v in data.get("events", {}).values())
    print(f"Loaded cache: {total} events, cached at {data.get('cached_at', '?')}")
    return data


# ---------------------------------------------------------------------------
# Transaction classification
# ---------------------------------------------------------------------------

def classify_transactions(
    vault_events: dict,
    decimals_token0: int,
    decimals_token1: int,
    include_withdrawal_fees: bool = True,
) -> dict:
    """
    Classify vault transactions into types based on pool events in each tx.
    - rebalance: has real Burns (liquidity > 0) AND Mints
    - claim: has zero-liquidity Burns + Collects (fee collection only)
    - deposit: has Mints but no real Burns
    - withdrawal: has real Burns but no Mints
    Returns {categories: {tx_hash: category}, claim_fees: [f0, f1], rebal_fees: [f0, f1]}.

    If include_withdrawal_fees is True, fees from withdrawal txs (Collect - Burn)
    are added to rebal_fees.
    """
    tx_ev = defaultdict(lambda: {"Mint": [], "Burn": [], "Collect": []})
    for etype in ["Mint", "Burn", "Collect"]:
        for e in vault_events.get(etype, []):
            tx = e.get("tx_hash", "").lower()
            if tx:
                tx_ev[tx][etype].append(e)

    categories = {}
    claim_fees = [0.0, 0.0]
    rebal_fees = [0.0, 0.0]

    for tx, ev in tx_ev.items():
        has_mint = len(ev["Mint"]) > 0
        has_burn = len(ev["Burn"]) > 0
        has_collect = len(ev["Collect"]) > 0
        all_burns_zero = has_burn and all(
            int(b["args"]["amount"]) == 0 for b in ev["Burn"]
        )
        real_burn = has_burn and not all_burns_zero

        if real_burn and has_mint:
            categories[tx] = "rebalance"
            # Fees from rebalance = Collect - Burn
            for c in ev["Collect"]:
                rebal_fees[0] += int(c["args"]["amount0"]) / 10**decimals_token0
                rebal_fees[1] += int(c["args"]["amount1"]) / 10**decimals_token1
            for b in ev["Burn"]:
                rebal_fees[0] -= int(b["args"]["amount0"]) / 10**decimals_token0
                rebal_fees[1] -= int(b["args"]["amount1"]) / 10**decimals_token1
        elif (all_burns_zero or not has_burn) and has_collect and not has_mint:
            categories[tx] = "claim"
            for c in ev["Collect"]:
                claim_fees[0] += int(c["args"]["amount0"]) / 10**decimals_token0
                claim_fees[1] += int(c["args"]["amount1"]) / 10**decimals_token1
        elif has_mint and not real_burn:
            categories[tx] = "deposit"
        elif real_burn and not has_mint:
            categories[tx] = "withdrawal"
            if include_withdrawal_fees:
                # Fees from withdrawal = Collect - Burn (same as rebalance)
                for c in ev["Collect"]:
                    rebal_fees[0] += int(c["args"]["amount0"]) / 10**decimals_token0
                    rebal_fees[1] += int(c["args"]["amount1"]) / 10**decimals_token1
                for b in ev["Burn"]:
                    rebal_fees[0] -= int(b["args"]["amount0"]) / 10**decimals_token0
                    rebal_fees[1] -= int(b["args"]["amount1"]) / 10**decimals_token1

    return {
        "categories": categories,
        "claim_fees": claim_fees,
        "rebal_fees": [max(0, f) for f in rebal_fees],
    }


# ---------------------------------------------------------------------------
# Position timeline
# ---------------------------------------------------------------------------

def build_position_timeline(
    vault_events: dict,
    decimals_token0: int,
    decimals_token1: int,
    price_fn: Callable[[int, int], tuple[float, float]],
) -> pd.DataFrame:
    """
    Build a timeline of Mint/Burn events for a vault.

    price_fn(tick_lower, tick_upper) -> (price_lower, price_upper)
    handles pool-specific price direction (direct vs inverted).
    """
    rows = []
    for event_type in ["Mint", "Burn"]:
        for evt in vault_events.get(event_type, []):
            args = evt.get("args", {})
            tick_lower = int(args.get("tickLower", 0))
            tick_upper = int(args.get("tickUpper", 0))
            amount0 = int(args.get("amount0", 0))
            amount1 = int(args.get("amount1", 0))
            liquidity = int(args.get("amount", 0))

            # Skip zero-liquidity burns (fee accounting only)
            if event_type == "Burn" and liquidity == 0:
                continue

            # Skip full-range positions (MIN_TICK to MAX_TICK)
            if abs(tick_lower) > 500000 or abs(tick_upper) > 500000:
                continue

            price_lower, price_upper = price_fn(tick_lower, tick_upper)

            rows.append({
                "block": evt.get("block", 0),
                "tx_hash": evt.get("tx_hash", ""),
                "event_type": event_type,
                "tick_lower": tick_lower,
                "tick_upper": tick_upper,
                "price_lower": price_lower,
                "price_upper": price_upper,
                "range_width_pct": ((price_upper - price_lower) / price_lower * 100)
                                   if price_lower > 0 else 0,
                "amount0": amount0 / 10**decimals_token0,
                "amount1": amount1 / 10**decimals_token1,
                "liquidity": liquidity,
            })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("block").reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Range segments for visualization
# ---------------------------------------------------------------------------

def build_range_segments(timeline: pd.DataFrame) -> list[dict]:
    """
    From Mint/Burn timeline, build range segments with start/end blocks.
    Each segment = a position active from Mint to Burn (or still active).
    """
    active: dict[tuple, list] = {}
    segments = []

    for _, row in timeline.iterrows():
        key = (row["tick_lower"], row["tick_upper"])

        if row["event_type"] == "Mint":
            if key not in active:
                active[key] = []
            active[key].append({
                "start_block": row["block"],
                "liquidity": row["liquidity"],
                "price_lower": row["price_lower"],
                "price_upper": row["price_upper"],
                "range_width_pct": row["range_width_pct"],
            })

        elif row["event_type"] == "Burn":
            if key in active and active[key]:
                pos = active[key].pop(0)
                segments.append({
                    "start_block": pos["start_block"],
                    "end_block": row["block"],
                    "tick_lower": key[0],
                    "tick_upper": key[1],
                    "price_lower": pos["price_lower"],
                    "price_upper": pos["price_upper"],
                    "range_width_pct": pos["range_width_pct"],
                    "liquidity": pos["liquidity"],
                    "closed": True,
                })
                if not active[key]:
                    del active[key]

    # Still-active positions
    for key, positions in active.items():
        for pos in positions:
            segments.append({
                "start_block": pos["start_block"],
                "end_block": None,
                "tick_lower": key[0],
                "tick_upper": key[1],
                "price_lower": pos["price_lower"],
                "price_upper": pos["price_upper"],
                "range_width_pct": pos["range_width_pct"],
                "liquidity": pos["liquidity"],
                "closed": False,
            })

    return segments


# ---------------------------------------------------------------------------
# Price timeline from Swap events
# ---------------------------------------------------------------------------

def build_price_timeline(
    swap_events: list,
    sqrt_price_fn: Callable[[int], float],
) -> pd.DataFrame:
    """
    Build price timeline from Swap events.

    sqrt_price_fn(sqrtPriceX96) -> float converts to human-readable price.
    """
    rows = []
    for evt in swap_events:
        args = evt.get("args", {})
        sqrt_price = int(args.get("sqrtPriceX96", 0))
        if sqrt_price > 0:
            price = sqrt_price_fn(sqrt_price)
            rows.append({
                "block": evt.get("block", 0),
                "price": price,
                "tick": int(args.get("tick", 0)),
            })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("block").reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Block → timestamp mapping
# ---------------------------------------------------------------------------

def build_block_timestamps(blocks: list[int]) -> dict[int, datetime]:
    """Build a mapping from block numbers to datetimes (sampled + interpolated)."""
    unique = sorted(set(blocks))
    if not unique:
        return {}

    # Sample ~60 blocks for timestamp lookups
    if len(unique) > 60:
        step = max(1, len(unique) // 60)
        sample = unique[::step]
        if unique[-1] not in sample:
            sample.append(unique[-1])
        if unique[0] not in sample:
            sample.insert(0, unique[0])
    else:
        sample = unique

    print(f"  Fetching timestamps for {len(sample)} sampled blocks...")
    block_ts = {}
    for b in sample:
        try:
            block_ts[b] = get_block_timestamp(b)
        except Exception:
            pass

    # Interpolate
    if len(block_ts) >= 2:
        sorted_known = sorted(block_ts.items())
        for b in unique:
            if b not in block_ts:
                for i in range(len(sorted_known) - 1):
                    b0, t0 = sorted_known[i]
                    b1, t1 = sorted_known[i + 1]
                    if b0 <= b <= b1:
                        frac = (b - b0) / (b1 - b0) if b1 != b0 else 0
                        block_ts[b] = int(t0 + frac * (t1 - t0))
                        break

    return {b: datetime.fromtimestamp(ts, tz=timezone.utc) for b, ts in block_ts.items()}

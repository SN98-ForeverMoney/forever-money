"""
ALM Vault Analysis — Side-by-side comparison of ForeverMoney vs Arrakis vs Arcadia.
Fetches on-chain Mint/Burn/Collect/Swap events, builds position timelines,
and produces an interactive HTML dashboard.

Usage:
    python analysis/vault_analysis.py

Output:
    analysis/output/vaults_dashboard.html
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
from web3 import Web3

# ---------------------------------------------------------------------------
# Project imports
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from validator.utils.math import UniswapV3Math  # noqa: E402

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_RPC = os.environ.get("BASE_RPC", "https://base-mainnet.public.blastapi.io")
w3 = Web3(Web3.HTTPProvider(BASE_RPC, request_kwargs={"timeout": 30}))

VAULTS = {
    "Arrakis": "0x203a29615F8E83d8eFbfA839f486Be0fa17a75b5",
    "Arcadia 1": "0xeb202299f8b1aBcEA945Ec19aF72381057f4B453",
    "Arcadia 2": "0xAF3DC9Ff470B162800E8E6C4aDF7ad06f8438EEd",
    "ForeverMoney": "0x215968271599156a3298CF4BCb16517F687F013b",
    "vfat": "0xBCd06b460e9ec8B202984cdCf14CB8f0FD263e24",
}

# Pool address: WETH/BID Aerodrome CL on Base
POOL_ADDRESS = "0x1024C20c048ea6087293f46D4a1C042CB6705924"

# NFT Position Manager
POSITION_MANAGER = "0x827922686190790b37229fd06084350E74485b72"

# Arrakis Pro module (AerodromeStandardModulePrivate) — Collect recipient for all
# Arrakis positions. Used to discover tx hashes not visible via Blockscout Safe queries.
ARRAKIS_MODULE = "0x5b1D02aaed93EdE69D6E0dD6Bf44f066Df07BedA"

# Comparison window start (FM vault start)
START_DATE = datetime(2026, 3, 9, tzinfo=timezone.utc)

# Extended window: capture position openings from all vaults (Arrakis/Arcadia active since Dec 2025)
POSITIONS_START_DATE = datetime(2025, 11, 30, tzinfo=timezone.utc)

# Paths
ANALYSIS_DIR = Path(__file__).resolve().parent
CACHE_DIR = ANALYSIS_DIR / "data"
OUTPUT_DIR = ANALYSIS_DIR / "output"
CACHE_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

ABI_DIR = PROJECT_ROOT / "validator" / "utils" / "abis"

# Token decimals (WETH=18, BID=18)
DECIMALS_TOKEN0 = 18
DECIMALS_TOKEN1 = 18

BLOCKSCOUT_BASE = "https://base.blockscout.com/api/v2"

# ---------------------------------------------------------------------------
# ABI loading
# ---------------------------------------------------------------------------

def load_abi(name: str) -> list:
    with open(ABI_DIR / f"{name}.json") as f:
        data = json.load(f)
    return data["abi"] if isinstance(data, dict) and "abi" in data else data


POOL_ABI = load_abi("ICLPool")

# ---------------------------------------------------------------------------
# Block helpers
# ---------------------------------------------------------------------------

def get_block_timestamp(block_num: int) -> int:
    block = w3.eth.get_block(block_num)
    return block["timestamp"]


def find_block_for_timestamp(target_ts: int, lo: int = 1, hi: int | None = None) -> int:
    """Binary search for the block closest to a target timestamp."""
    if hi is None:
        hi = w3.eth.block_number
    while lo < hi:
        mid = (lo + hi) // 2
        ts = get_block_timestamp(mid)
        if ts < target_ts:
            lo = mid + 1
        else:
            hi = mid
    return lo


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
    Token-transfers captures Burns/Collects (tokens flow to vault).
    Transactions captures Mints (vault initiates the tx).
    """
    addr = vault_address.lower()
    tx_hashes = set()

    # Token transfers (captures most events)
    tx_hashes |= _fetch_blockscout_paginated(
        f"{BLOCKSCOUT_BASE}/addresses/{addr}/token-transfers",
        tx_hash_key="transaction_hash",
    )

    # Direct transactions (captures Mint txs where vault is sender/caller)
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


def fetch_all_pool_events(pool_address: str, from_block: int, to_block: int) -> dict:
    """Fetch Mint, Burn, Collect, Swap events from a pool."""
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(pool_address), abi=POOL_ABI
    )

    event_types = {
        "Mint": contract.events.Mint,
        "Burn": contract.events.Burn,
        "Collect": contract.events.Collect,
        "Swap": contract.events.Swap,
    }

    results = {}
    for name, event_class in event_types.items():
        topic = event_class().build_filter().topics[0]
        if isinstance(topic, bytes):
            topic = "0x" + topic.hex()

        print(f"  Fetching {name} events...")
        raw_logs = fetch_logs_chunked(pool_address, [topic], from_block, to_block)
        print(f"    Got {len(raw_logs)} raw {name} logs")

        decoded = []
        for log in raw_logs:
            try:
                evt = event_class().process_log(log)
                decoded.append(evt)
            except Exception:
                pass
        print(f"    Decoded {len(decoded)} {name} events")
        results[name] = decoded

    return results


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _serialize(v: Any) -> Any:
    if isinstance(v, bytes):
        return "0x" + v.hex()
    if isinstance(v, int) and abs(v) > 2**53:
        return str(v)
    return v


def events_to_serializable(raw_events: dict) -> dict:
    """Convert web3 event objects to JSON-serializable dicts."""
    events = {}
    for event_type, event_list in raw_events.items():
        events[event_type] = [
            {
                "block": evt["blockNumber"],
                "tx_hash": ("0x" + evt["transactionHash"].hex()
                            if isinstance(evt["transactionHash"], bytes)
                            else evt["transactionHash"]).lower(),
                "log_index": evt["logIndex"],
                "args": {k: _serialize(v) for k, v in evt["args"].items()},
            }
            for evt in event_list
        ]
    return events


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def save_cache(pool_events: dict, vault_tx_map: dict):
    """Save pool events and vault tx hashes to cache."""
    cache_file = CACHE_DIR / "vaults_pool_events.json"
    data = {
        "pool": POOL_ADDRESS,
        "events": pool_events,
        "vault_tx_hashes": {name: sorted(txs) for name, txs in vault_tx_map.items()},
        "cached_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    with open(cache_file, "w") as f:
        json.dump(data, f, indent=2)
    total = sum(len(v) for v in pool_events.values())
    print(f"Cached {total} events + vault tx hashes to {cache_file}")


def load_cache() -> dict | None:
    cache_file = CACHE_DIR / "vaults_pool_events.json"
    if not cache_file.exists():
        return None
    with open(cache_file) as f:
        data = json.load(f)
    total = sum(len(v) for v in data.get("events", {}).values())
    print(f"Loaded cache: {total} events, cached at {data.get('cached_at', '?')}")
    return data


# ---------------------------------------------------------------------------
# Match events to vaults by tx_hash
# ---------------------------------------------------------------------------

def match_events_to_vaults(pool_events: dict, vault_tx_map: dict) -> dict:
    """
    Match pool events to vaults using transaction hashes.
    vault_tx_map: {vault_name: set_of_tx_hashes}
    """
    result = {name: {"Mint": [], "Burn": [], "Collect": [], "Swap": []}
              for name in VAULTS}

    # Build reverse lookup: tx_hash -> vault_name
    tx_to_vault = {}
    for name, tx_hashes in vault_tx_map.items():
        for tx in tx_hashes:
            tx_to_vault[tx.lower()] = name

    for event_type in ["Mint", "Burn", "Collect", "Swap"]:
        for evt in pool_events.get(event_type, []):
            tx = evt.get("tx_hash", "").lower()
            if tx in tx_to_vault:
                vault_name = tx_to_vault[tx]
                result[vault_name][event_type].append(evt)

    # Print summary
    print("\nEvent matching summary (by tx_hash):")
    for name in VAULTS:
        counts = {k: len(v) for k, v in result[name].items()}
        total = sum(counts.values())
        print(f"  {name}: {counts} (total: {total})")

    # Unmatched events
    all_matched_txs = set(tx_to_vault.keys())
    for event_type in ["Mint", "Burn", "Collect"]:
        unmatched = [e for e in pool_events.get(event_type, [])
                     if e.get("tx_hash", "").lower() not in all_matched_txs]
        if unmatched:
            print(f"  Unmatched {event_type}: {len(unmatched)}")

    return result


def discover_arrakis_tx_hashes(pool_events: dict) -> set:
    """Discover Arrakis tx hashes via Collect recipient == Arrakis Pro module.

    Arrakis Pro creates positions through its module contract, so Mint txs
    don't appear in Blockscout for the Safe address. But Collect events have
    the module as ``recipient``, letting us find all Arrakis txs from cached
    pool data alone — no extra API calls.
    """
    module = ARRAKIS_MODULE.lower()
    return {
        evt["tx_hash"].lower()
        for evt in pool_events.get("Collect", [])
        if evt.get("args", {}).get("recipient", "").lower() == module
    }


# ---------------------------------------------------------------------------
# Build position timeline
# ---------------------------------------------------------------------------

def tick_to_price(tick: int) -> float:
    """Convert tick to human-readable price."""
    sqrt = UniswapV3Math.get_sqrt_ratio_at_tick(tick)
    return UniswapV3Math.sqrt_price_x96_to_price(sqrt, DECIMALS_TOKEN0, DECIMALS_TOKEN1)


def build_position_timeline(vault_events: dict) -> pd.DataFrame:
    """Build a timeline of Mint/Burn events for a vault."""
    rows = []
    for event_type in ["Mint", "Burn"]:
        for evt in vault_events.get(event_type, []):
            args = evt.get("args", {})
            tick_lower = int(args.get("tickLower", 0))
            tick_upper = int(args.get("tickUpper", 0))
            amount0 = int(args.get("amount0", 0))
            amount1 = int(args.get("amount1", 0))
            liquidity = int(args.get("amount", 0))

            # Skip zero-liquidity burns (fee accounting only, not real position changes)
            if event_type == "Burn" and liquidity == 0:
                continue

            # Skip full-range positions (MIN_TICK to MAX_TICK) — these produce
            # extreme prices and are typically cleanup/initialization events
            if abs(tick_lower) > 500000 or abs(tick_upper) > 500000:
                continue

            price_lower = tick_to_price(tick_lower)
            price_upper = tick_to_price(tick_upper)

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
                "amount0": amount0 / 10**DECIMALS_TOKEN0,
                "amount1": amount1 / 10**DECIMALS_TOKEN1,
                "liquidity": liquidity,
            })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("block").reset_index(drop=True)
    return df


def build_fee_timeline(vault_events: dict) -> pd.DataFrame:
    """Build fee collection timeline."""
    rows = []
    for evt in vault_events.get("Collect", []):
        args = evt.get("args", {})
        rows.append({
            "block": evt.get("block", 0),
            "amount0": int(args.get("amount0", 0)) / 10**DECIMALS_TOKEN0,
            "amount1": int(args.get("amount1", 0)) / 10**DECIMALS_TOKEN1,
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("block").reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Build range segments for visualization
# ---------------------------------------------------------------------------

def build_range_segments(timeline: pd.DataFrame, earliest_block: int = 0) -> list[dict]:
    """
    From Mint/Burn timeline, build range segments with start/end blocks.
    Each segment = a position that was active from Mint to Burn (or still active).
    Burns without matching Mints are shown as "inferred" (position existed before our data).
    """
    # Track active positions: {(tick_lower, tick_upper): [{start_block, liquidity}]}
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
                # Close the oldest matching position
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
            # Burn without matching Mint — skip (position was created
            # before our data window or through untracked contracts)

    # Still-active positions
    for key, positions in active.items():
        for pos in positions:
            segments.append({
                "start_block": pos["start_block"],
                "end_block": None,  # still active
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

def build_price_timeline(swap_events: list) -> pd.DataFrame:
    rows = []
    for evt in swap_events:
        args = evt.get("args", {})
        sqrt_price = int(args.get("sqrtPriceX96", 0))
        if sqrt_price > 0:
            price = UniswapV3Math.sqrt_price_x96_to_price(
                sqrt_price, DECIMALS_TOKEN0, DECIMALS_TOKEN1
            )
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


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def classify_transactions(vault_events: dict) -> dict:
    """
    Classify vault transactions into types based on pool events in each tx.
    - rebalance: has real Burns (liquidity > 0) AND Mints
    - claim: has zero-liquidity Burns + Collects (fee collection only)
    - deposit: has Mints but no real Burns
    - withdrawal: has real Burns but no Mints
    Returns {tx_hash: category} and fee totals.
    """
    from collections import defaultdict
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
                rebal_fees[0] += int(c["args"]["amount0"]) / 10**DECIMALS_TOKEN0
                rebal_fees[1] += int(c["args"]["amount1"]) / 10**DECIMALS_TOKEN1
            for b in ev["Burn"]:
                rebal_fees[0] -= int(b["args"]["amount0"]) / 10**DECIMALS_TOKEN0
                rebal_fees[1] -= int(b["args"]["amount1"]) / 10**DECIMALS_TOKEN1
        elif (all_burns_zero or not has_burn) and has_collect and not has_mint:
            categories[tx] = "claim"
            for c in ev["Collect"]:
                claim_fees[0] += int(c["args"]["amount0"]) / 10**DECIMALS_TOKEN0
                claim_fees[1] += int(c["args"]["amount1"]) / 10**DECIMALS_TOKEN1
        elif has_mint and not real_burn:
            categories[tx] = "deposit"
        elif real_burn and not has_mint:
            categories[tx] = "withdrawal"

    return {
        "categories": categories,
        "claim_fees": claim_fees,
        "rebal_fees": [max(0, f) for f in rebal_fees],
    }


def compute_metrics(
    timeline: pd.DataFrame,
    fees_df: pd.DataFrame,
    swaps: list,
    vault_name: str = "",
    vault_events: dict | None = None,
) -> dict:
    mints = timeline[timeline["event_type"] == "Mint"] if not timeline.empty else pd.DataFrame()
    burns = timeline[timeline["event_type"] == "Burn"] if not timeline.empty else pd.DataFrame()

    # Classify transactions
    tx_info = classify_transactions(vault_events or {})
    cat_counts = {}
    for cat in tx_info["categories"].values():
        cat_counts[cat] = cat_counts.get(cat, 0) + 1

    rebalance_count = cat_counts.get("rebalance", 0)
    claim_count = cat_counts.get("claim", 0)
    deposit_count = cat_counts.get("deposit", 0)
    withdrawal_count = cat_counts.get("withdrawal", 0)

    # Use Mints for range width; fall back to Burns if no Mints
    if not mints.empty:
        avg_range_width = mints["range_width_pct"].mean()
    elif not burns.empty:
        avg_range_width = burns["range_width_pct"].mean()
    else:
        avg_range_width = 0

    # Positions per rebalance (e.g. Arrakis uses 2: wide + narrow)
    if not mints.empty:
        mints_per_block = mints.groupby("block").size()
        positions_per_rebalance = round(mints_per_block.median())
    else:
        positions_per_rebalance = 0

    # Fees from proper classification
    total_fees_0 = tx_info["claim_fees"][0] + tx_info["rebal_fees"][0]
    total_fees_1 = tx_info["claim_fees"][1] + tx_info["rebal_fees"][1]

    # Determine if vault uses swaps
    uses_swaps = "Arcadia" in vault_name or vault_name == "vfat"

    return {
        "rebalance_count": rebalance_count,
        "claim_count": claim_count,
        "deposit_count": deposit_count,
        "withdrawal_count": withdrawal_count,
        "total_mints": len(mints),
        "total_burns": len(burns),
        "avg_range_width_pct": round(avg_range_width, 2),
        "uses_swaps": uses_swaps,
        "swap_count": len(swaps),
        "total_fees_token0": round(total_fees_0, 6),
        "total_fees_token1": round(total_fees_1, 2),
        "claim_fees_token0": round(tx_info["claim_fees"][0], 6),
        "claim_fees_token1": round(tx_info["claim_fees"][1], 2),
        "total_deposited_0": round(mints["amount0"].sum(), 6) if not mints.empty else 0,
        "total_deposited_1": round(mints["amount1"].sum(), 2) if not mints.empty else 0,
        "positions_per_rebalance": int(positions_per_rebalance),
    }


# ---------------------------------------------------------------------------
# Dashboard HTML
# ---------------------------------------------------------------------------

def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    """Convert hex color to rgba string."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


VAULT_COLORS = {
    "Arrakis": "#636EFA",
    "Arcadia 1": "#EF553B",
    "Arcadia 2": "#FFA15A",
    "ForeverMoney": "#00CC96",
    "vfat": "#AB63FA",
}


def build_dashboard(
    vault_timelines: dict[str, pd.DataFrame],
    vault_fees: dict[str, pd.DataFrame],
    vault_metrics: dict[str, dict],
    vault_segments: dict[str, list[dict]],
    price_df: pd.DataFrame,
    block_ts: dict[int, datetime],
    cached_at: str = "",
):
    def bdt(block: int) -> datetime:
        """Block to datetime with fallback."""
        if block in block_ts:
            return block_ts[block]
        known = sorted(block_ts.keys())
        if not known:
            return START_DATE
        closest = min(known, key=lambda b: abs(b - block))
        return block_ts[closest]

    now_dt = datetime.now(tz=timezone.utc)
    vault_names = list(VAULTS.keys())
    n = len(vault_names)

    # ===== CHART 1: Swimlane Range Timeline =====
    fig_swim = make_subplots(
        rows=n, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        subplot_titles=vault_names,
    )

    for i, name in enumerate(vault_names):
        row = i + 1
        color = VAULT_COLORS.get(name, "#999")
        segments = vault_segments.get(name, [])

        for seg in segments:
            start_dt = bdt(seg["start_block"])
            end_dt = bdt(seg["end_block"]) if seg["end_block"] else now_dt

            # Draw filled rectangle for range
            fig_swim.add_trace(
                go.Scatter(
                    x=[start_dt, end_dt, end_dt, start_dt, start_dt],
                    y=[seg["price_lower"], seg["price_lower"],
                       seg["price_upper"], seg["price_upper"], seg["price_lower"]],
                    fill="toself",
                    fillcolor=color,
                    opacity=0.6,
                    line=dict(color=color, width=1),
                    showlegend=False,
                    hovertemplate=(
                        f"<b>{name}</b><br>"
                        f"Range: {seg['price_lower']:,.0f} – {seg['price_upper']:,.0f} BID/ETH<br>"
                        f"Width: {seg['range_width_pct']:.1f}%<br>"
                        f"Time: %{{x}}<br>"
                        f"{'Active' if not seg['closed'] else 'Closed'}"
                        "<extra></extra>"
                    ),
                ),
                row=row, col=1,
            )

        # Price overlay
        if not price_df.empty:
            # Downsample price for performance
            step = max(1, len(price_df) // 500)
            sampled = price_df.iloc[::step]
            times = [bdt(b) for b in sampled["block"]]
            fig_swim.add_trace(
                go.Scatter(
                    x=times, y=sampled["price"],
                    mode="lines",
                    line=dict(color="rgba(255,255,255,0.4)", width=1, dash="dot"),
                    showlegend=False,
                    hoverinfo="skip",
                ),
                row=row, col=1,
            )

    fig_swim.update_layout(
        title=dict(text="How Each Vault Positions Liquidity", font=dict(size=20)),
        height=180 * n + 80,
        template="plotly_dark",
        showlegend=False,
        margin=dict(l=80, r=40, t=80, b=40),
    )
    for i in range(n):
        fig_swim.update_yaxes(title_text="BID/ETH", row=i + 1, col=1)

    # ===== CHART 2: Fees Collected =====
    names_list = list(vault_metrics.keys())
    fees_vals = [m["total_fees_token0"] for m in vault_metrics.values()]
    fees_vals_t1 = [m["total_fees_token1"] for m in vault_metrics.values()]

    fig_perf = make_subplots(
        rows=1, cols=2,
        subplot_titles=["WETH Fees", "BID Fees"],
        horizontal_spacing=0.12,
    )

    fig_perf.add_trace(go.Bar(
        name="WETH",
        x=names_list,
        y=fees_vals,
        marker_color="#00CC96",
        text=[f"{v:.4f}" for v in fees_vals],
        textposition="outside",
        showlegend=False,
    ), row=1, col=1)

    fig_perf.add_trace(go.Bar(
        name="BID",
        x=names_list,
        y=fees_vals_t1,
        marker_color="#636EFA",
        text=[f"{v:.0f}" for v in fees_vals_t1],
        textposition="outside",
        showlegend=False,
    ), row=1, col=2)

    fig_perf.update_layout(
        title=dict(text="Fees Collected", font=dict(size=20)),
        template="plotly_dark",
        height=400,
        margin=dict(l=80, r=40, t=80, b=40),
    )

    # ===== CHART 3: Range Width Comparison =====
    fig_width = go.Figure()
    for name in vault_names:
        tl = vault_timelines.get(name, pd.DataFrame())
        if tl.empty:
            continue
        mints = tl[tl["event_type"] == "Mint"].copy()
        if mints.empty:
            continue
        color = VAULT_COLORS.get(name, "#999")
        per_block = mints.groupby("block").agg(
            wide=("range_width_pct", "max"),
            narrow=("range_width_pct", "min"),
            count=("range_width_pct", "size"),
        ).reset_index()
        is_multi = (per_block["count"] > 1).any()
        times = [bdt(b) for b in per_block["block"]]

        if is_multi:
            fig_width.add_trace(go.Scatter(
                x=times, y=per_block["wide"],
                mode="lines+markers",
                name=f"{name} (wide)",
                line=dict(color=color), marker=dict(size=5),
                hovertemplate=f"<b>{name} (wide)</b><br>Width: %{{y:.0f}}%<br>%{{x}}<extra></extra>",
            ))
            fig_width.add_trace(go.Scatter(
                x=times, y=per_block["narrow"],
                mode="lines+markers",
                name=f"{name} (narrow)",
                line=dict(color=color, dash="dash"), marker=dict(size=5),
                hovertemplate=f"<b>{name} (narrow)</b><br>Width: %{{y:.0f}}%<br>%{{x}}<extra></extra>",
            ))
        else:
            fig_width.add_trace(go.Scatter(
                x=times, y=per_block["wide"],
                mode="lines+markers",
                name=name,
                line=dict(color=color), marker=dict(size=6),
                hovertemplate=f"<b>{name}</b><br>Width: %{{y:.0f}}%<br>%{{x}}<extra></extra>",
            ))

    fig_width.update_layout(
        title=dict(text="Range Tightness Over Time", font=dict(size=20)),
        template="plotly_dark",
        height=400,
        yaxis_title="Range Width (%)",
        margin=dict(l=80, r=40, t=80, b=60),
        hovermode="closest",
    )

    # ===== CHART 3b: Current Position Snapshot =====
    # Get current price from price_df
    current_price_val = price_df["price"].iloc[-1] if not price_df.empty else 0

    # Find all active positions per vault
    active_positions = {}
    for name in vault_names:
        segs = vault_segments.get(name, [])
        active = [s for s in segs if not s["closed"]]
        if active:
            active_positions[name] = active

    snapshot_vaults = [n for n in vault_names if n in active_positions]
    n_snap = len(snapshot_vaults)

    fig_snapshot = make_subplots(
        rows=n_snap, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.12,
    )

    for i, name in enumerate(snapshot_vaults):
        row = i + 1
        positions = active_positions[name]
        color = VAULT_COLORS.get(name, "#999")
        n_pos = len(positions)

        for j, seg in enumerate(positions):
            # Stack bars vertically: each position gets its own y-band
            y_lo = j / n_pos
            y_hi = (j + 1) / n_pos - 0.05  # small gap between bars
            is_wide = n_pos > 1 and seg["range_width_pct"] == max(s["range_width_pct"] for s in positions)
            label = " (wide)" if is_wide else (" (narrow)" if n_pos > 1 else "")
            in_range = seg["price_lower"] <= current_price_val <= seg["price_upper"]
            range_status = "In range" if in_range else "OUT OF RANGE"
            bar_opacity = 0.6 if (n_pos == 1 or is_wide) else 0.4
            line_style = dict(color=color, width=1) if in_range else dict(color=color, width=2, dash="dash")

            fig_snapshot.add_trace(
                go.Scatter(
                    x=[seg["price_lower"], seg["price_upper"], seg["price_upper"],
                       seg["price_lower"], seg["price_lower"]],
                    y=[y_lo, y_lo, y_hi, y_hi, y_lo],
                    fill="toself",
                    fillcolor=color if in_range else _hex_to_rgba(color, 0.15),
                    opacity=bar_opacity,
                    line=line_style,
                    showlegend=False,
                    hovertemplate=(
                        f"<b>{name}{label}</b><br>"
                        f"Range: {seg['price_lower']:,.0f} – {seg['price_upper']:,.0f} BID/ETH<br>"
                        f"Width: {seg['range_width_pct']:.1f}%<br>"
                        f"{range_status}"
                        "<extra></extra>"
                    ),
                ),
                row=row, col=1,
            )

        # Add vault name as centered text
        widest = max(positions, key=lambda s: s["range_width_pct"])
        mid_price = (widest["price_lower"] + widest["price_upper"]) / 2
        fig_snapshot.add_trace(
            go.Scatter(
                x=[mid_price],
                y=[0.5],
                mode="text",
                text=[name],
                textfont=dict(color="white", size=13),
                showlegend=False,
                hoverinfo="skip",
            ),
            row=row, col=1,
        )

        # Add current price line in each row
        if current_price_val > 0:
            fig_snapshot.add_trace(
                go.Scatter(
                    x=[current_price_val, current_price_val],
                    y=[0, 1],
                    mode="lines",
                    line=dict(color="white", width=2, dash="dash"),
                    showlegend=False,
                    hovertemplate=f"Current price: {current_price_val:,.0f} BID/ETH<extra></extra>",
                ),
                row=row, col=1,
            )

        # Hide y-axis
        fig_snapshot.update_yaxes(visible=False, row=row, col=1)

    # Compute x-axis range with padding
    all_lowers = [s["price_lower"] for n in snapshot_vaults for s in active_positions[n]]
    all_uppers = [s["price_upper"] for n in snapshot_vaults for s in active_positions[n]]
    x_min = min(all_lowers) * 0.85 if all_lowers else 0
    x_max = max(all_uppers) * 1.15 if all_uppers else 1

    fig_snapshot.update_layout(
        title=dict(text="Current Active Ranges vs Price", font=dict(size=20)),
        template="plotly_dark",
        height=350,
        margin=dict(l=40, r=40, t=80, b=40),
        hovermode="closest",
    )
    # Apply x range to all subplots
    for i in range(n_snap):
        fig_snapshot.update_xaxes(tickformat=",", range=[x_min, x_max], row=i + 1, col=1)
    fig_snapshot.update_xaxes(title_text="BID / ETH", row=n_snap, col=1)

    # ===== TABLE =====
    table_headers = ["Metric"] + list(vault_metrics.keys())
    rows_data = [
        ["Strategy Style"] + [
            "Aggressive" if m["uses_swaps"]
            else ("Passive" if m["rebalance_count"] <= 5 else "Moderate")
            for m in vault_metrics.values()
        ],
        ["Range Tightness (avg)"] + [
            f'{m["avg_range_width_pct"]:.1f}%' for m in vault_metrics.values()
        ],
        ["Positions / Rebalance"] + [
            str(m["positions_per_rebalance"]) for m in vault_metrics.values()
        ],
        ["Rebalances"] + [
            str(m["rebalance_count"]) for m in vault_metrics.values()
        ],
        ["Fee Claims"] + [
            str(m["claim_count"]) for m in vault_metrics.values()
        ],
        ["Deposits"] + [
            str(m["deposit_count"]) for m in vault_metrics.values()
        ],
        ["Withdrawals"] + [
            str(m["withdrawal_count"]) for m in vault_metrics.values()
        ],
        ["Uses Swaps?"] + [
            "Yes" if m["uses_swaps"] else "No" for m in vault_metrics.values()
        ],
        ["Total Fees (WETH)"] + [
            f'{m["total_fees_token0"]:.6f}' for m in vault_metrics.values()
        ],
        ["Total Fees (BID)"] + [
            f'{m["total_fees_token1"]:.0f}' for m in vault_metrics.values()
        ],
        ["Fee per Rebalance (WETH)"] + [
            f'{m["total_fees_token0"] / max(m["rebalance_count"], 1):.6f}'
            for m in vault_metrics.values()
        ],
    ]

    fig_table = go.Figure(data=[go.Table(
        header=dict(
            values=table_headers,
            fill_color="#2d2d2d",
            font=dict(color="white", size=13),
            align="left",
        ),
        cells=dict(
            values=[[r[i] for r in rows_data] for i in range(len(table_headers))],
            fill_color=["#1e1e1e"] + [
                _hex_to_rgba(VAULT_COLORS.get(n, "#333333"), 0.13)
                for n in vault_metrics.keys()
            ],
            font=dict(color="white", size=12),
            align="left",
            height=30,
        ),
    )])
    fig_table.update_layout(
        title=dict(text="Detailed Comparison", font=dict(size=20)),
        template="plotly_dark",
        height=80 + 32 * len(rows_data),
        margin=dict(l=20, r=20, t=60, b=20),
    )

    # ===== BUILD HTML =====
    html = [
        "<!DOCTYPE html><html><head>",
        '<meta charset="utf-8">',
        "<title>ALM Vault Analysis</title>",
        "<style>",
        "body { background: #111; color: #eee; font-family: -apple-system, BlinkMacSystemFont, sans-serif; padding: 20px 40px; max-width: 1400px; margin: 0 auto; }",
        "h1 { text-align: center; font-size: 28px; margin-bottom: 4px; }",
        ".subtitle { text-align: center; color: #888; font-size: 14px; margin-bottom: 32px; }",
        "h2 { margin-top: 48px; border-bottom: 1px solid #333; padding-bottom: 8px; font-size: 20px; }",
        ".cards { display: flex; gap: 16px; flex-wrap: wrap; margin: 20px 0; }",
        ".card { background: #1a1a1a; border-radius: 10px; padding: 20px; flex: 1; min-width: 220px; }",
        ".card h3 { margin: 0 0 12px 0; font-size: 16px; }",
        ".card .style { font-size: 28px; font-weight: 700; margin-bottom: 8px; }",
        ".card .detail { font-size: 13px; color: #aaa; line-height: 1.6; }",
        ".chart-row { display: flex; gap: 16px; flex-wrap: wrap; }",
        ".chart-half { flex: 1; min-width: 400px; }",
        "</style>",
        "</head><body>",
        "<h1>ALM Vault Analysis</h1>",
        f'<div class="subtitle">WETH / BID on Base (Aerodrome CL) &mdash; {POSITIONS_START_DATE.strftime("%b %d, %Y")} to {cached_at}</div>',

        # --- Behavior cards ---
        '<h2>Vault Behavior at a Glance</h2>',
        '<div class="cards">',
    ]

    for name, m in vault_metrics.items():
        color = VAULT_COLORS.get(name, "#999")
        style = ("Aggressive" if m["uses_swaps"]
                 else ("Passive" if m["rebalance_count"] <= 5 else "Moderate"))
        swap_text = f'{m["swap_count"]} swaps' if m["uses_swaps"] else "No swaps"
        claims_text = f'{m["claim_count"]} fee claims' if m["claim_count"] > 0 else "No fee claims"

        # Vault-specific notes
        note = ""
        if name == "Arcadia 2":
            note = '<br><span style="color:#FFA15A;font-size:11px;">Staked in Aerodrome gauge — earns AERO rewards instead of pool fees</span>'
        elif name == "Arrakis":
            note = '<br><span style="color:#636EFA;font-size:11px;">2-position strategy (wide + narrow) · Staked in Aerodrome gauge</span>'
        elif name == "vfat":
            note = '<br><span style="color:#AB63FA;font-size:11px;">Per-user Sickle smart account · Uses swaps on deposit</span>'

        html.append(
            f'<div class="card" style="border-top: 3px solid {color};">'
            f'<h3>{name}</h3>'
            f'<div class="style">{style}</div>'
            f'<div class="detail">'
            f'Rebalanced <b>{m["rebalance_count"]}x</b><br>'
            f'Avg range width: <b>{m["avg_range_width_pct"]:.1f}%</b><br>'
            f'{swap_text} | {claims_text}<br>'
            f'Fees: {m["total_fees_token0"]:.4f} WETH + {m["total_fees_token1"]:.0f} BID'
            f'{note}'
            f'</div></div>'
        )

    html.append("</div>")

    # --- Swimlane ---
    html.append('<h2>Range Positioning Over Time</h2>')
    html.append('<p style="color:#888;font-size:13px;">Colored bars show active liquidity ranges. Dotted white line = market price. Gaps between bars = rebalance events.</p>')
    html.append(fig_swim.to_html(full_html=False, include_plotlyjs="cdn"))

    # --- Range width ---
    html.append('<h2>Current Active Ranges vs Price</h2>')
    html.append('<p style="color:#888;font-size:13px;">Where each vault has its liquidity right now. Dashed white line = current market price. Wider bar = more spread out capital.</p>')
    html.append(fig_snapshot.to_html(full_html=False, include_plotlyjs=False))

    html.append('<h2>Range Tightness Over Time</h2>')
    html.append('<p style="color:#888;font-size:13px;">How wide each vault\'s range is when they open a new position. Lower = tighter = more concentrated liquidity.</p>')
    html.append(fig_width.to_html(full_html=False, include_plotlyjs=False))

    # --- Performance ---
    html.append('<h2>Fees Collected</h2>')
    html.append(fig_perf.to_html(full_html=False, include_plotlyjs=False))

    # --- IL + Net Return ---
    html.append('<h2>IL + Net Return</h2>')
    html.append('<p style="color:#888;font-size:13px;">Impermanent loss vs fees earned. Only Arcadia 1 has complete deposit/withdrawal data for a full calculation.</p>')
    html.append("""
    <div class="cards">
      <div class="card" style="border-top: 3px solid #EF553B; min-width: 300px;">
        <h3>Arcadia 1 — Full Performance</h3>
        <div style="display:grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 12px;">
          <div>
            <div class="label">Net Capital In</div>
            <div style="font-size:18px;">7.25 WETH + 520K BID</div>
          </div>
          <div>
            <div class="label">Current Balance</div>
            <div style="font-size:18px;">3.40 WETH + 1.04M BID</div>
          </div>
          <div>
            <div class="label">Price Change</div>
            <div style="font-size:18px;">+13.8%</div>
          </div>
          <div>
            <div class="label">Period</div>
            <div style="font-size:18px;">Dec 1 → Present (~116 days)</div>
          </div>
        </div>
        <div style="display:grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; margin-top: 16px; padding-top: 16px; border-top: 1px solid #333;">
          <div>
            <div class="label">Fees Earned</div>
            <div style="font-size:22px; color:#00CC96; font-weight:700;">+0.44 WETH-eq</div>
          </div>
          <div>
            <div class="label">Price Movement Cost (IL)</div>
            <div style="font-size:22px; color:#EF553B; font-weight:700;">-1.85 WETH-eq</div>
          </div>
          <div>
            <div class="label">Net Return</div>
            <div style="font-size:22px; color:#EF553B; font-weight:700;">-15.3%</div>
          </div>
        </div>
        <div style="margin-top: 12px; color: #888; font-size: 12px;">
          Fees don't cover the IL from a 13.8% price move. Wide ranges (609%) reduce IL per move but also reduce fee capture.
        </div>
      </div>

      <div class="card" style="min-width: 250px;">
        <h3>Other Vaults</h3>
        <div style="margin-top: 12px; line-height: 2;">
          <div><b style="color:#FFA15A;">Arcadia 2:</b> Staked in gauge — earns AERO rewards (different token). Pool fee data not comparable.</div>
          <div><b style="color:#636EFA;">Arrakis:</b> Staked in gauge — earns AERO rewards (different token). Pool fee data not comparable.</div>
          <div><b style="color:#00CC96;">ForeverMoney:</b> Active only 17 days with minimal test capital (~0.0003 WETH). Too early for meaningful IL comparison.</div>
          <div><b style="color:#AB63FA;">vfat:</b> Single deposit via Sickle smart account. No rebalances yet — too early for IL comparison.</div>
        </div>
      </div>
    </div>
    """)

    # --- Table ---
    html.append('<h2>Side-by-Side Comparison</h2>')
    html.append(fig_table.to_html(full_html=False, include_plotlyjs=False))

    # --- Methodology ---
    html.append("""
    <h2>How This Was Calculated</h2>
    <div style="color:#aaa; font-size:13px; line-height:1.8; max-width:900px;">

    <h3 style="color:#eee; font-size:16px; margin-top:24px;">Data Source</h3>
    <p>All data comes directly from on-chain events on the <b>WETH/BID Aerodrome CL pool</b> on Base
    (<code style="color:#888;">0x1024C20c...05924</code>). Events were fetched via RPC <code>eth_getLogs</code>
    from Nov 30, 2025 to """ + cached_at + """. Each vault's transactions were identified via the
    <a href="https://base.blockscout.com" style="color:#636EFA;">Blockscout API</a> and matched to pool events by transaction hash.</p>

    <h3 style="color:#eee; font-size:16px; margin-top:24px;">Range Data</h3>
    <p>Every time a vault opens a position, a <b>Mint</b> event is emitted with the exact tick range
    (lower and upper price bounds). When a position is closed, a <b>Burn</b> event is emitted.
    The swimlane chart shows these as colored bars — each bar is one active position from Mint to Burn.
    Range width % = (upper price − lower price) / lower price × 100.</p>

    <h3 style="color:#eee; font-size:16px; margin-top:24px;">Transaction Classification</h3>
    <table style="border-collapse:collapse; margin:8px 0;">
      <tr style="border-bottom:1px solid #333;">
        <td style="padding:6px 16px 6px 0;"><b>Rebalance</b></td>
        <td style="padding:6px 0;">Transaction contains both Burn (close old position) and Mint (open new position)</td>
      </tr>
      <tr style="border-bottom:1px solid #333;">
        <td style="padding:6px 16px 6px 0;"><b>Fee Claim</b></td>
        <td style="padding:6px 0;">Transaction has a zero-liquidity Burn + Collect. This is how Uniswap V3 fee claims work — you call burn(0) to trigger fee accounting, then collect the fees.</td>
      </tr>
      <tr style="border-bottom:1px solid #333;">
        <td style="padding:6px 16px 6px 0;"><b>Deposit</b></td>
        <td style="padding:6px 0;">Transaction has a Mint but no Burns — new capital entering the pool</td>
      </tr>
      <tr>
        <td style="padding:6px 16px 6px 0;"><b>Withdrawal</b></td>
        <td style="padding:6px 0;">Transaction has Burns but no Mints — capital leaving the pool</td>
      </tr>
    </table>
    <p>Validated against Arcadia's own frontend — rebalance counts match exactly (21 for Arcadia 1, 25 for Arcadia 2).</p>

    <h3 style="color:#eee; font-size:16px; margin-top:24px;">Fee Calculation</h3>
    <p>Fees come from two sources:</p>
    <ul style="margin:4px 0;">
      <li><b>Fee claim transactions:</b> The Collect amounts are pure fees (no principal mixed in, since the Burn was zero-liquidity).</li>
      <li><b>Rebalance transactions:</b> Fees = Collect amounts − Burn amounts. The Collect includes both the returned principal and accumulated fees, so subtracting the Burn (principal only) isolates the fees.</li>
    </ul>
    <p>Total fees = claim fees + rebalance fees.</p>

    <h3 style="color:#eee; font-size:16px; margin-top:24px;">IL + Net Return (Arcadia 1 only)</h3>
    <p>Calculated using actual ERC-20 deposit and withdrawal transactions from Blockscout:</p>
    <ul style="margin:4px 0;">
      <li><b>Net capital in</b> = Total deposited − Total withdrawn (in WETH + BID)</li>
      <li><b>HODL value</b> = What the net capital would be worth today if just held (no LP). BID converted to WETH-equivalent at current pool price.</li>
      <li><b>LP value</b> = Current vault balance as reported on Arcadia frontend (~Mar 16): 3.4 WETH + 1.04M BID</li>
      <li><b>IL</b> = HODL value − LP value (the cost of being an LP instead of just holding)</li>
      <li><b>Net return</b> = (LP value + Fees − HODL value) / HODL value</li>
    </ul>

    <h3 style="color:#eee; font-size:16px; margin-top:24px;">Data Limitations</h3>
    <table style="border-collapse:collapse; margin:8px 0;">
      <tr style="border-bottom:1px solid #333;">
        <td style="padding:6px 16px 6px 0;"><b>Arrakis</b></td>
        <td style="padding:6px 0;">Positions traced via Arrakis Pro module (Collect recipient). Initial deposit positions (before Dec 2025 data window) not captured; all rebalances tracked.</td>
      </tr>
      <tr style="border-bottom:1px solid #333;">
        <td style="padding:6px 16px 6px 0;"><b>Arcadia 2</b></td>
        <td style="padding:6px 0;">Stakes LP positions in Aerodrome gauge — earns AERO token rewards instead of pool trading fees. The 0 fee number doesn't mean 0 income.</td>
      </tr>
      <tr style="border-bottom:1px solid #333;">
        <td style="padding:6px 16px 6px 0;"><b>ForeverMoney</b></td>
        <td style="padding:6px 0;">Operating with minimal test capital (~0.0003 WETH per position) for ~17 days. Fee and IL numbers are too small for meaningful comparison.</td>
      </tr>
      <tr>
        <td style="padding:6px 16px 6px 0;"><b>vfat</b></td>
        <td style="padding:6px 0;">Single deposit via Sickle smart account, no rebalances yet. Too early for meaningful performance comparison.</td>
      </tr>
    </table>

    </div>
    """)

    html.append("</body></html>")

    output_path = OUTPUT_DIR / "vaults_dashboard.html"
    with open(output_path, "w") as f:
        f.write("\n".join(html))
    print(f"\nDashboard saved to: {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("ALM Vault Analysis")
    print("=" * 60)

    cached = load_cache()

    if cached and "vault_tx_hashes" in cached:
        pool_events = cached["events"]
        vault_tx_map = {name: set(txs) for name, txs in cached["vault_tx_hashes"].items()}
        print("Using cached data.")

        # Arrakis Pro module discovery (works on cached data, no API calls)
        arrakis_module_txs = discover_arrakis_tx_hashes(pool_events)
        old_count = len(vault_tx_map.get("Arrakis", set()))
        vault_tx_map["Arrakis"] = vault_tx_map.get("Arrakis", set()) | arrakis_module_txs
        new_found = len(vault_tx_map["Arrakis"]) - old_count
        if new_found:
            print(f"  Arrakis Pro module discovery: {new_found} additional txs")
    else:
        # Step 1: Get vault tx hashes from Blockscout
        print("\nFetching vault transaction hashes from Blockscout...")
        vault_tx_map = {}
        for name, addr in VAULTS.items():
            print(f"  {name} ({addr[:10]}...)...")
            txs = get_vault_tx_hashes(addr)
            vault_tx_map[name] = txs
            print(f"    {len(txs)} unique transactions")

        # Step 2: Find block ranges
        # Extended range for Mint/Burn/Collect (captures Dec 2025 positions)
        print(f"\nFinding blocks for date ranges...")
        positions_ts = int(POSITIONS_START_DATE.timestamp())
        positions_from_block = find_block_for_timestamp(positions_ts)
        comparison_ts = int(START_DATE.timestamp())
        comparison_from_block = find_block_for_timestamp(comparison_ts)
        to_block = w3.eth.block_number
        print(f"Positions range: {positions_from_block} - {to_block} ({to_block - positions_from_block:,} blocks)")
        print(f"Comparison range: {comparison_from_block} - {to_block} ({to_block - comparison_from_block:,} blocks)")

        # Step 3: Fetch pool events
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(POOL_ADDRESS), abi=POOL_ABI
        )

        pool_events = {}

        # Mint/Burn/Collect from extended range (captures all position history)
        # Use larger chunks for the older period since events are sparse
        for event_name in ["Mint", "Burn", "Collect"]:
            event_class = getattr(contract.events, event_name)
            topic = event_class().build_filter().topics[0]
            if isinstance(topic, bytes):
                topic = "0x" + topic.hex()

            print(f"\n  Fetching {event_name} events (from {POSITIONS_START_DATE.strftime('%Y-%m-%d')})...")
            raw_logs = fetch_logs_chunked(
                POOL_ADDRESS, [topic], positions_from_block, to_block,
                chunk_size=50_000,  # Larger chunks — these events are sparse
            )
            print(f"    Got {len(raw_logs)} raw logs")

            decoded = []
            for log in raw_logs:
                try:
                    evt = event_class().process_log(log)
                    decoded.append(evt)
                except Exception:
                    pass
            print(f"    Decoded {len(decoded)} events")
            pool_events[event_name] = [
                {
                    "block": evt["blockNumber"],
                    "tx_hash": ("0x" + evt["transactionHash"].hex()
                                if isinstance(evt["transactionHash"], bytes)
                                else evt["transactionHash"]).lower(),
                    "log_index": evt["logIndex"],
                    "args": {k: _serialize(v) for k, v in evt["args"].items()},
                }
                for evt in decoded
            ]

        # Swap events only from comparison window (for price timeline)
        event_class = contract.events.Swap
        topic = event_class().build_filter().topics[0]
        if isinstance(topic, bytes):
            topic = "0x" + topic.hex()

        print(f"\n  Fetching Swap events (from {START_DATE.strftime('%Y-%m-%d')})...")
        raw_logs = fetch_logs_chunked(
            POOL_ADDRESS, [topic], comparison_from_block, to_block,
            chunk_size=10_000,
        )
        print(f"    Got {len(raw_logs)} raw logs")

        decoded = []
        for log in raw_logs:
            try:
                evt = event_class().process_log(log)
                decoded.append(evt)
            except Exception:
                pass
        print(f"    Decoded {len(decoded)} events")
        pool_events["Swap"] = [
            {
                "block": evt["blockNumber"],
                "tx_hash": ("0x" + evt["transactionHash"].hex()
                            if isinstance(evt["transactionHash"], bytes)
                            else evt["transactionHash"]).lower(),
                "log_index": evt["logIndex"],
                "args": {k: _serialize(v) for k, v in evt["args"].items()},
            }
            for evt in decoded
        ]

        # Arrakis Pro module discovery
        arrakis_module_txs = discover_arrakis_tx_hashes(pool_events)
        new_found = len(arrakis_module_txs - vault_tx_map.get("Arrakis", set()))
        vault_tx_map["Arrakis"] = vault_tx_map.get("Arrakis", set()) | arrakis_module_txs
        if new_found:
            print(f"  Arrakis Pro module discovery: {new_found} additional txs")

        # Cache
        save_cache(pool_events, vault_tx_map)

    # Step 4: Match events to vaults
    print("\nMatching events to vaults...")
    vault_events = match_events_to_vaults(pool_events, vault_tx_map)

    # Step 5: Build timelines
    print("\nBuilding timelines...")
    vault_timelines = {}
    vault_fees = {}
    vault_segments = {}

    for name in VAULTS:
        vault_timelines[name] = build_position_timeline(vault_events[name])
        vault_fees[name] = build_fee_timeline(vault_events[name])
        # Use positions start block as earliest reference for inferred segments
        positions_from = int(POSITIONS_START_DATE.timestamp())
        vault_segments[name] = build_range_segments(
            vault_timelines[name],
            earliest_block=38835727,  # ~Nov 30, 2025
        )
        tl = vault_timelines[name]
        if not tl.empty:
            mints = len(tl[tl["event_type"] == "Mint"])
            burns = len(tl[tl["event_type"] == "Burn"])
            segs = len(vault_segments[name])
            print(f"  {name}: {mints} mints, {burns} burns, {segs} range segments")
        else:
            print(f"  {name}: no events")

    # Price timeline
    print("\nBuilding price timeline...")
    price_df = build_price_timeline(pool_events.get("Swap", []))
    print(f"  {len(price_df)} price points")

    # Metrics
    print("\nComputing metrics...")
    vault_metrics = {}
    for name in VAULTS:
        vault_metrics[name] = compute_metrics(
            vault_timelines[name], vault_fees[name], vault_events[name]["Swap"],
            vault_name=name,
            vault_events=vault_events[name],
        )

    # Print summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    header = f"{'Metric':<25}" + "".join(f"{n:>15}" for n in vault_metrics.keys())
    print(header)
    print("-" * len(header))
    metric_keys = [
        ("Strategy", lambda m: "Aggressive" if m["uses_swaps"] else ("Passive" if m["rebalance_count"] <= 5 else "Moderate")),
        ("Rebalances", lambda m: str(m["rebalance_count"])),
        ("Fee Claims", lambda m: str(m["claim_count"])),
        ("Deposits", lambda m: str(m["deposit_count"])),
        ("Withdrawals", lambda m: str(m["withdrawal_count"])),
        ("Avg Range Width", lambda m: f'{m["avg_range_width_pct"]:.1f}%'),
        ("Positions/Rebalance", lambda m: str(m["positions_per_rebalance"])),
        ("Uses Swaps", lambda m: "Yes" if m["uses_swaps"] else "No"),
        ("Total Fees (WETH)", lambda m: f'{m["total_fees_token0"]:.6f}'),
        ("Total Fees (BID)", lambda m: f'{m["total_fees_token1"]:.0f}'),
        ("Claim Fees (WETH)", lambda m: f'{m["claim_fees_token0"]:.6f}'),
    ]
    for label, fn in metric_keys:
        row = f"{label:<25}" + "".join(f"{fn(m):>15}" for m in vault_metrics.values())
        print(row)

    # Timestamps
    all_blocks = set()
    for name in VAULTS:
        if not vault_timelines[name].empty:
            all_blocks.update(vault_timelines[name]["block"].tolist())
    if not price_df.empty:
        all_blocks.update(price_df["block"].tolist())

    print(f"\nBuilding timestamp map for {len(all_blocks)} blocks...")
    block_ts = build_block_timestamps(list(all_blocks))

    # Dashboard
    print("\nGenerating dashboard...")
    # Get snapshot timestamp from cache or current time
    if cached:
        snapshot_ts = cached.get("cached_at", "")
        if snapshot_ts:
            try:
                dt = datetime.fromisoformat(snapshot_ts)
                snapshot_str = dt.strftime("%b %d, %Y at %H:%M UTC")
            except Exception:
                snapshot_str = snapshot_ts
        else:
            snapshot_str = datetime.now(tz=timezone.utc).strftime("%b %d, %Y at %H:%M UTC")
    else:
        snapshot_str = datetime.now(tz=timezone.utc).strftime("%b %d, %Y at %H:%M UTC")

    path = build_dashboard(
        vault_timelines, vault_fees, vault_metrics,
        vault_segments, price_df, block_ts,
        cached_at=snapshot_str,
    )
    print(f"\nDone! Open {path} in a browser.")


if __name__ == "__main__":
    main()

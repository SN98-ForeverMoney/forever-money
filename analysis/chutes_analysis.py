"""
Chutes Capital Efficiency Dashboard — xSN64/USDC Aerodrome CL pool.
Compares our vault's fee capture vs share of pool TVL.

Usage:
    python analysis/chutes_analysis.py

Output:
    analysis/output/chutes_dashboard.html
"""

import json
import os
import sys
import time
from collections import defaultdict
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

# Pool: xSN64/USDC Aerodrome CL (Slipstream) on Base
POOL_ADDRESS = "0x72764A83B78074e517fd1E2da8C4c289020C6498"
SLIPSTREAM_PM = "0xa990C6a764b73BF43cee5Bb40339c3322FB9D55F"

# Vault 1 (protocol vault — the one we track)
VAULT_ADDRESS = "0x785e630baa05d2B7f6185c7A2A9910Ca8A2e41fd"
VAULT_LM = "0x41B3247B2cb408e49A8061aA65519Caad5bd489d"

# Tokens
USDC_ADDRESS = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
XSN64_ADDRESS = "0xbAdd3F2d84605032C1B2AD8cBebb4700Dcd9D7dE"
DECIMALS_TOKEN0 = 6   # USDC
DECIMALS_TOKEN1 = 18  # xSN64
TOKEN0_SYMBOL = "USDC"
TOKEN1_SYMBOL = "xSN64"

# Block range — start before first known activity (Vault 1 Mint at 43825338)
START_BLOCK = 43700000

# Paths
ANALYSIS_DIR = Path(__file__).resolve().parent
CACHE_DIR = ANALYSIS_DIR / "data"
OUTPUT_DIR = ANALYSIS_DIR / "output"
CACHE_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

ABI_DIR = PROJECT_ROOT / "validator" / "utils" / "abis"

BLOCKSCOUT_BASE = "https://base.blockscout.com/api/v2"

# Vault color
VAULT_COLOR = "#00CC96"
POOL_COLOR = "#636EFA"

# ---------------------------------------------------------------------------
# ABI loading
# ---------------------------------------------------------------------------

def load_abi(name: str) -> list:
    with open(ABI_DIR / f"{name}.json") as f:
        data = json.load(f)
    return data["abi"] if isinstance(data, dict) and "abi" in data else data


POOL_ABI = load_abi("ICLPool")

# Minimal ABI for pool state queries (may not be in ICLPool.json)
POOL_STATE_ABI = [
    {"inputs": [], "name": "fee", "outputs": [{"type": "uint24"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "slot0", "outputs": [
        {"name": "sqrtPriceX96", "type": "uint160"},
        {"name": "tick", "type": "int24"},
        {"name": "observationIndex", "type": "uint16"},
        {"name": "observationCardinality", "type": "uint16"},
        {"name": "observationCardinalityNext", "type": "uint16"},
        {"name": "unlocked", "type": "bool"},
    ], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "liquidity", "outputs": [{"type": "uint128"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "tickSpacing", "outputs": [{"type": "int24"}], "stateMutability": "view", "type": "function"},
]

ERC20_ABI = [
    {"inputs": [{"name": "account", "type": "address"}], "name": "balanceOf",
     "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
]


# ---------------------------------------------------------------------------
# Block helpers
# ---------------------------------------------------------------------------

def get_block_timestamp(block_num: int) -> int:
    block = w3.eth.get_block(block_num)
    return block["timestamp"]


# ---------------------------------------------------------------------------
# Blockscout: get tx hashes
# ---------------------------------------------------------------------------

def _fetch_blockscout_paginated(url: str, tx_hash_key: str = "transaction_hash") -> set[str]:
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
# RPC event fetching
# ---------------------------------------------------------------------------

def fetch_logs_chunked(
    pool_address: str,
    topics: list,
    from_block: int,
    to_block: int,
    chunk_size: int = 10_000,
) -> list:
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


def fetch_all_pool_events(from_block: int, to_block: int) -> dict:
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(POOL_ADDRESS), abi=POOL_ABI
    )

    results = {}
    event_types = {
        "Mint": contract.events.Mint,
        "Burn": contract.events.Burn,
        "Collect": contract.events.Collect,
        "Swap": contract.events.Swap,
    }

    for name, event_class in event_types.items():
        topic = event_class().build_filter().topics[0]
        if isinstance(topic, bytes):
            topic = "0x" + topic.hex()

        chunk = 50_000 if name != "Swap" else 10_000
        print(f"  Fetching {name} events...")
        raw_logs = fetch_logs_chunked(POOL_ADDRESS, [topic], from_block, to_block, chunk_size=chunk)
        print(f"    Got {len(raw_logs)} raw {name} logs")

        decoded = []
        for log in raw_logs:
            try:
                evt = event_class().process_log(log)
                decoded.append(evt)
            except Exception:
                pass
        print(f"    Decoded {len(decoded)} {name} events")

        results[name] = [
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

    return results


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
# Pool state queries
# ---------------------------------------------------------------------------

def get_pool_state() -> dict:
    """Query current pool state: fee tier, slot0, liquidity, tick spacing."""
    pool = w3.eth.contract(
        address=Web3.to_checksum_address(POOL_ADDRESS),
        abi=POOL_STATE_ABI,
    )
    fee_tier = pool.functions.fee().call()
    slot0 = pool.functions.slot0().call()
    liquidity = pool.functions.liquidity().call()
    tick_spacing = pool.functions.tickSpacing().call()

    print("  Fetching pool TVL from DexScreener...")
    pool_tvl_usd = get_pool_tvl_usd()
    print(f"  Pool TVL: ${pool_tvl_usd:,.0f}")

    return {
        "fee_tier": fee_tier,
        "sqrtPriceX96": str(slot0[0]),
        "tick": slot0[1],
        "liquidity": str(liquidity),
        "tick_spacing": tick_spacing,
        "pool_tvl_usd": pool_tvl_usd,
    }


def get_pool_tvl_usd() -> float:
    """Fetch pool TVL in USD from DexScreener API."""
    url = f"https://api.dexscreener.com/latest/dex/pairs/base/{POOL_ADDRESS}"
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        pairs = data.get("pairs", [])
        if pairs:
            return float(pairs[0].get("liquidity", {}).get("usd", 0))
    except Exception as e:
        print(f"  Warning: DexScreener API failed: {e}")
    return 0.0


def get_vault_balances() -> dict:
    """Get idle token balances held by the vault."""
    usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_ADDRESS), abi=ERC20_ABI)
    xsn64 = w3.eth.contract(address=Web3.to_checksum_address(XSN64_ADDRESS), abi=ERC20_ABI)

    vault_cs = Web3.to_checksum_address(VAULT_ADDRESS)
    usdc_bal = usdc.functions.balanceOf(vault_cs).call()
    xsn64_bal = xsn64.functions.balanceOf(vault_cs).call()

    return {
        "usdc": usdc_bal / 10**DECIMALS_TOKEN0,
        "xsn64": xsn64_bal / 10**DECIMALS_TOKEN1,
    }


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

CACHE_FILE = CACHE_DIR / "chutes_pool_events.json"


def save_cache(pool_events: dict, vault_tx_hashes: set, pool_state: dict, vault_balances: dict):
    data = {
        "pool": POOL_ADDRESS,
        "events": pool_events,
        "vault_tx_hashes": sorted(vault_tx_hashes),
        "pool_state": pool_state,
        "vault_balances": vault_balances,
        "cached_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f, indent=2)
    total = sum(len(v) for v in pool_events.values())
    print(f"Cached {total} events to {CACHE_FILE}")


def load_cache() -> dict | None:
    if not CACHE_FILE.exists():
        return None
    with open(CACHE_FILE) as f:
        data = json.load(f)
    total = sum(len(v) for v in data.get("events", {}).values())
    print(f"Loaded cache: {total} events, cached at {data.get('cached_at', '?')}")
    return data


# ---------------------------------------------------------------------------
# Vault tx hash discovery
# ---------------------------------------------------------------------------

def discover_vault_tx_hashes(collect_events: list) -> set[str]:
    """
    Discover Vault 1 tx hashes via two methods:
    1. Collect recipient matching (primary — works from RPC data alone)
    2. Blockscout on the LiquidityManager address (secondary)
    """
    vault_addr = VAULT_ADDRESS.lower()

    # Method 1: Collect events where recipient == vault address
    recipient_txs = {
        evt["tx_hash"].lower()
        for evt in collect_events
        if evt.get("args", {}).get("recipient", "").lower() == vault_addr
    }
    print(f"  Collect recipient matching: {len(recipient_txs)} tx hashes")

    # Method 2: Blockscout on LM address
    print(f"  Fetching LM tx hashes from Blockscout ({VAULT_LM[:10]}...)...")
    lm_txs = get_vault_tx_hashes(VAULT_LM)
    print(f"    {len(lm_txs)} tx hashes from Blockscout")

    combined = recipient_txs | lm_txs
    print(f"  Combined: {len(combined)} unique tx hashes")
    return combined


# ---------------------------------------------------------------------------
# Event matching & classification
# ---------------------------------------------------------------------------

def match_vault_events(pool_events: dict, vault_tx_hashes: set) -> dict:
    """Filter pool events to only those belonging to our vault."""
    result = {"Mint": [], "Burn": [], "Collect": [], "Swap": []}
    tx_set = {tx.lower() for tx in vault_tx_hashes}

    for event_type in ["Mint", "Burn", "Collect"]:
        for evt in pool_events.get(event_type, []):
            if evt.get("tx_hash", "").lower() in tx_set:
                result[event_type].append(evt)

    counts = {k: len(v) for k, v in result.items()}
    print(f"  Vault events: {counts}")
    return result


def classify_transactions(vault_events: dict) -> dict:
    """Classify vault txs: rebalance, claim, deposit, withdrawal."""
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
            # Fees from withdrawal = Collect - Burn (same as rebalance)
            for c in ev["Collect"]:
                rebal_fees[0] += int(c["args"]["amount0"]) / 10**DECIMALS_TOKEN0
                rebal_fees[1] += int(c["args"]["amount1"]) / 10**DECIMALS_TOKEN1
            for b in ev["Burn"]:
                rebal_fees[0] -= int(b["args"]["amount0"]) / 10**DECIMALS_TOKEN0
                rebal_fees[1] -= int(b["args"]["amount1"]) / 10**DECIMALS_TOKEN1

    return {
        "categories": categories,
        "claim_fees": claim_fees,
        "rebal_fees": [max(0, f) for f in rebal_fees],
    }


# ---------------------------------------------------------------------------
# Price helpers
# ---------------------------------------------------------------------------

def tick_to_price(tick: int) -> float:
    """Convert tick to USDC per xSN64 price.

    Note: In this pool, higher tick = more xSN64/USDC = LOWER USDC/xSN64.
    So tick_lower maps to a HIGHER USDC price and tick_upper to a LOWER one.
    """
    sqrt = UniswapV3Math.get_sqrt_ratio_at_tick(tick)
    # sqrt_price_x96_to_price returns token1/token0 = xSN64/USDC
    # We want USDC/xSN64, so invert
    raw = UniswapV3Math.sqrt_price_x96_to_price(sqrt, DECIMALS_TOKEN0, DECIMALS_TOKEN1)
    return 1.0 / raw if raw > 0 else 0


def sqrt_price_to_usdc_per_xsn64(sqrt_price_x96: int) -> float:
    """Convert sqrtPriceX96 to USDC per xSN64."""
    raw = UniswapV3Math.sqrt_price_x96_to_price(sqrt_price_x96, DECIMALS_TOKEN0, DECIMALS_TOKEN1)
    return 1.0 / raw if raw > 0 else 0


# ---------------------------------------------------------------------------
# Capital efficiency calculations
# ---------------------------------------------------------------------------

def compute_swap_fees(swap_events: list, fee_tier: int) -> pd.DataFrame:
    """
    Compute implied fees from each Swap event.
    Fee = input_amount * fee_tier / 1_000_000
    (Swap event amounts include the fee — the positive amount is the gross input)
    """
    rows = []
    for evt in swap_events:
        args = evt.get("args", {})
        amount0 = int(args.get("amount0", 0))
        amount1 = int(args.get("amount1", 0))
        sqrt_price = int(args.get("sqrtPriceX96", 0))
        block = evt.get("block", 0)

        if sqrt_price <= 0:
            continue

        price = sqrt_price_to_usdc_per_xsn64(sqrt_price)

        # Positive amount = gross input (includes fee)
        fee_token0 = 0.0
        fee_token1 = 0.0
        if amount0 > 0:
            fee_token0 = (amount0 * fee_tier / 1_000_000) / 10**DECIMALS_TOKEN0
        if amount1 > 0:
            fee_token1 = (amount1 * fee_tier / 1_000_000) / 10**DECIMALS_TOKEN1

        # Convert to USD (token0 is USDC, so fee_token0 is already in USD)
        fee_usd = fee_token0 + fee_token1 * price

        rows.append({
            "block": block,
            "fee_token0": fee_token0,
            "fee_token1": fee_token1,
            "fee_usd": fee_usd,
            "price": price,
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("block").reset_index(drop=True)
    return df


def compute_capital_efficiency(
    vault_events: dict,
    swap_fees_df: pd.DataFrame,
    pool_state: dict,
    swap_events: list,
) -> dict:
    """Compute capital efficiency metrics for our vault."""
    tx_info = classify_transactions(vault_events)
    cat_counts = {}
    for cat in tx_info["categories"].values():
        cat_counts[cat] = cat_counts.get(cat, 0) + 1

    # Vault fees
    vault_fee_usdc = tx_info["claim_fees"][0] + tx_info["rebal_fees"][0]
    vault_fee_xsn64 = tx_info["claim_fees"][1] + tx_info["rebal_fees"][1]

    # Current price from pool state
    current_sqrt_price = int(pool_state["sqrtPriceX96"])
    current_price = sqrt_price_to_usdc_per_xsn64(current_sqrt_price)
    current_tick = pool_state["tick"]
    pool_liquidity = int(pool_state["liquidity"])

    # Find vault position(s) from Mint events
    vault_mints = vault_events.get("Mint", [])
    vault_burns = vault_events.get("Burn", [])

    position_info = None
    vault_liquidity = 0
    position_start = None
    position_end = None

    if vault_mints:
        mint = vault_mints[0]
        args = mint["args"]
        tick_lower = int(args.get("tickLower", 0))
        tick_upper = int(args.get("tickUpper", 0))
        vault_liquidity = int(args.get("amount", 0))
        position_start = mint["block"]

        # Find matching burn
        real_burns = [b for b in vault_burns if int(b["args"].get("amount", 0)) > 0]
        if real_burns:
            position_end = real_burns[-1]["block"]

        price_at_tl = tick_to_price(tick_lower)
        price_at_tu = tick_to_price(tick_upper)
        price_lower = min(price_at_tl, price_at_tu)
        price_upper = max(price_at_tl, price_at_tu)
        width_pct = (price_upper - price_lower) / price_lower * 100 if price_lower > 0 else 0

        deposited_usdc = int(args.get("amount0", 0)) / 10**DECIMALS_TOKEN0
        deposited_xsn64 = int(args.get("amount1", 0)) / 10**DECIMALS_TOKEN1

        position_info = {
            "tick_lower": tick_lower,
            "tick_upper": tick_upper,
            "price_lower": round(price_lower, 2),
            "price_upper": round(price_upper, 2),
            "width_pct": round(width_pct, 1),
            "deposited_usdc": round(deposited_usdc, 2),
            "deposited_xsn64": round(deposited_xsn64, 4),
            "deposited_usd": round(deposited_usdc + deposited_xsn64 * current_price, 2),
        }

    # TVL share: dollar-based (our deposit USD / pool TVL USD)
    # This is the meaningful metric — what % of pool dollars are ours.
    # Using L ratio would just confirm V3 distributes fees proportionally to L (obvious).
    # Dollar-based TVL share reveals capital efficiency from concentration.
    pool_tvl_usd = pool_state.get("pool_tvl_usd", 0)
    tvl_share_pct = 0.0
    deposit_usd = 0.0

    if position_info and pool_tvl_usd > 0:
        # Use price at time of deposit for deposit value
        nearby_swaps = [
            s for s in swap_events
            if abs(s.get("block", 0) - position_start) < 500
        ]
        if nearby_swaps:
            closest = min(nearby_swaps, key=lambda s: abs(s["block"] - position_start))
            price_at_deposit = sqrt_price_to_usdc_per_xsn64(int(closest["args"]["sqrtPriceX96"]))
        else:
            price_at_deposit = current_price

        deposited_usdc = int(vault_mints[0]["args"].get("amount0", 0)) / 10**DECIMALS_TOKEN0
        deposited_xsn64 = int(vault_mints[0]["args"].get("amount1", 0)) / 10**DECIMALS_TOKEN1
        deposit_usd = deposited_usdc + deposited_xsn64 * price_at_deposit
        tvl_share_pct = (deposit_usd / pool_tvl_usd) * 100

    # Fee share: vault fees / total pool fees during position period
    fee_share_pct = 0.0
    total_pool_fees_usd = 0.0
    total_pool_fees_all_time_usd = 0.0
    vault_fee_usd = vault_fee_usdc + vault_fee_xsn64 * current_price

    if not swap_fees_df.empty:
        total_pool_fees_all_time_usd = swap_fees_df["fee_usd"].sum()

        if position_start:
            end_block = position_end or (position_start + 10000)
            period_fees = swap_fees_df[
                (swap_fees_df["block"] >= position_start) &
                (swap_fees_df["block"] <= end_block)
            ]
            total_pool_fees_usd = period_fees["fee_usd"].sum()

            if total_pool_fees_usd > 0:
                fee_share_pct = (vault_fee_usd / total_pool_fees_usd) * 100

    # Efficiency ratio
    efficiency_ratio = (fee_share_pct / tvl_share_pct) if tvl_share_pct > 0 else 0.0

    has_active_position = position_end is None and position_start is not None

    return {
        "vault_status": "active" if has_active_position else "idle",
        "position": position_info,
        "position_blocks": {
            "start": position_start,
            "end": position_end,
            "duration": (position_end - position_start) if position_start and position_end else None,
        },
        "vault_liquidity": vault_liquidity,
        "pool_tvl_usd": pool_tvl_usd,
        "deposit_usd": round(deposit_usd, 2),
        "tvl_share_pct": round(tvl_share_pct, 4),
        "vault_fees": {
            "usdc": round(vault_fee_usdc, 6),
            "xsn64": round(vault_fee_xsn64, 6),
            "usd": round(vault_fee_usd, 4),
        },
        "total_pool_fees_usd": round(total_pool_fees_usd, 2),
        "total_pool_fees_all_time_usd": round(total_pool_fees_all_time_usd, 2),
        "fee_share_pct": round(fee_share_pct, 4),
        "efficiency_ratio": round(efficiency_ratio, 4),
        "current_price": round(current_price, 2),
        "current_tick": current_tick,
        "tx_categories": cat_counts,
    }


# ---------------------------------------------------------------------------
# Timeline builders
# ---------------------------------------------------------------------------

def build_position_timeline(vault_events: dict) -> pd.DataFrame:
    rows = []
    for event_type in ["Mint", "Burn"]:
        for evt in vault_events.get(event_type, []):
            args = evt.get("args", {})
            tick_lower = int(args.get("tickLower", 0))
            tick_upper = int(args.get("tickUpper", 0))
            amount0 = int(args.get("amount0", 0))
            amount1 = int(args.get("amount1", 0))
            liquidity = int(args.get("amount", 0))

            if event_type == "Burn" and liquidity == 0:
                continue
            if abs(tick_lower) > 500000 or abs(tick_upper) > 500000:
                continue

            # tick_lower → higher USDC price, tick_upper → lower USDC price (inverted)
            price_at_tick_lower = tick_to_price(tick_lower)
            price_at_tick_upper = tick_to_price(tick_upper)
            price_lower = min(price_at_tick_lower, price_at_tick_upper)
            price_upper = max(price_at_tick_lower, price_at_tick_upper)

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


def build_range_segments(timeline: pd.DataFrame) -> list[dict]:
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


def build_price_timeline(swap_events: list) -> pd.DataFrame:
    rows = []
    for evt in swap_events:
        args = evt.get("args", {})
        sqrt_price = int(args.get("sqrtPriceX96", 0))
        if sqrt_price > 0:
            price = sqrt_price_to_usdc_per_xsn64(sqrt_price)
            rows.append({
                "block": evt.get("block", 0),
                "price": price,
                "tick": int(args.get("tick", 0)),
            })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("block").reset_index(drop=True)
    return df


def build_block_timestamps(blocks: list[int]) -> dict[int, datetime]:
    unique = sorted(set(blocks))
    if not unique:
        return {}

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
# Dashboard HTML
# ---------------------------------------------------------------------------

def build_dashboard(
    efficiency: dict,
    vault_events: dict,
    pool_events: dict,
    swap_fees_df: pd.DataFrame,
    price_df: pd.DataFrame,
    segments: list[dict],
    block_ts: dict[int, datetime],
    pool_state: dict,
    vault_balances: dict,
    cached_at: str = "",
):
    now_dt = datetime.now(tz=timezone.utc)

    def bdt(block: int) -> datetime:
        if block in block_ts:
            return block_ts[block]
        known = sorted(block_ts.keys())
        if not known:
            return now_dt
        closest = min(known, key=lambda b: abs(b - block))
        return block_ts[closest]

    # Data range for subtitle
    if block_ts:
        sorted_blocks = sorted(block_ts.keys())
        first_block = sorted_blocks[0]
        last_block = sorted_blocks[-1]
        first_dt = block_ts[first_block]
        last_dt = block_ts[last_block]
        span = last_dt - first_dt
        days = span.days
        data_range_str = (
            f"Data: block {first_block:,} to {last_block:,} "
            f"({first_dt.strftime('%b %d')} to {last_dt.strftime('%b %d, %Y')} &mdash; {days} days)"
        )
    else:
        data_range_str = ""

    # ===== CHART 1: Position Timeline with Price =====
    fig_timeline = go.Figure()

    # Position range bands
    for seg in segments:
        start_dt = bdt(seg["start_block"])
        end_dt = bdt(seg["end_block"]) if seg["end_block"] else now_dt
        status = "Closed" if seg["closed"] else "Active"

        fig_timeline.add_trace(go.Scatter(
            x=[start_dt, end_dt, end_dt, start_dt, start_dt],
            y=[seg["price_lower"], seg["price_lower"],
               seg["price_upper"], seg["price_upper"], seg["price_lower"]],
            fill="toself",
            fillcolor=f"rgba(0,204,150,0.3)",
            line=dict(color=VAULT_COLOR, width=2),
            showlegend=False,
            hovertemplate=(
                f"<b>Vault 1 Position</b><br>"
                f"Range: ${seg['price_lower']:.2f} – ${seg['price_upper']:.2f}<br>"
                f"Width: {seg['range_width_pct']:.1f}%<br>"
                f"Status: {status}<br>"
                f"Time: %{{x}}"
                "<extra></extra>"
            ),
        ))

        # Vertical markers for open/close
        fig_timeline.add_vline(x=start_dt, line=dict(color=VAULT_COLOR, width=1, dash="dash"))
        if seg["closed"]:
            fig_timeline.add_vline(x=end_dt, line=dict(color="#EF553B", width=1, dash="dash"))

    # Price overlay
    if not price_df.empty:
        step = max(1, len(price_df) // 500)
        sampled = price_df.iloc[::step]
        times = [bdt(b) for b in sampled["block"]]
        fig_timeline.add_trace(go.Scatter(
            x=times, y=sampled["price"],
            mode="lines",
            line=dict(color="rgba(255,255,255,0.7)", width=2),
            name="Price (USDC/xSN64)",
            hovertemplate="$%{y:.2f}<extra>Price</extra>",
        ))

    fig_timeline.update_layout(
        title=dict(text="Position Timeline & Price", font=dict(size=20)),
        template="plotly_dark",
        height=400,
        yaxis_title="USDC per xSN64",
        margin=dict(l=80, r=40, t=80, b=40),
        hovermode="closest",
    )

    # ===== CHART 2: Cumulative Fee Comparison =====
    fig_fees = go.Figure()

    if not swap_fees_df.empty:
        swap_fees_sorted = swap_fees_df.sort_values("block")
        cum_fees = swap_fees_sorted["fee_usd"].cumsum()
        times = [bdt(b) for b in swap_fees_sorted["block"]]

        fig_fees.add_trace(go.Scatter(
            x=times, y=cum_fees,
            mode="lines",
            name="Total Pool Fees",
            line=dict(color=POOL_COLOR, width=2),
            hovertemplate="$%{y:.2f}<extra>Pool Fees</extra>",
        ))

        # Shade vault active period
        pos_blocks = efficiency["position_blocks"]
        if pos_blocks["start"]:
            start_dt = bdt(pos_blocks["start"])
            end_dt = bdt(pos_blocks["end"]) if pos_blocks["end"] else now_dt
            y_max = cum_fees.max() if len(cum_fees) > 0 else 100

            fig_fees.add_vrect(
                x0=start_dt, x1=end_dt,
                fillcolor="rgba(0,204,150,0.1)",
                line=dict(color=VAULT_COLOR, width=1, dash="dash"),
                annotation_text="Position Active",
                annotation_position="top left",
                annotation_font_color=VAULT_COLOR,
            )

        # Vault fees as a horizontal line at total vault fees
        if efficiency["vault_fees"]["usd"] > 0:
            fig_fees.add_hline(
                y=efficiency["vault_fees"]["usd"],
                line=dict(color=VAULT_COLOR, width=2, dash="dot"),
                annotation_text=f"Vault Fees: ${efficiency['vault_fees']['usd']:.4f}",
                annotation_font_color=VAULT_COLOR,
            )

    fig_fees.update_layout(
        title=dict(text="Cumulative Pool Fees (USD)", font=dict(size=20)),
        template="plotly_dark",
        height=400,
        yaxis_title="Cumulative Fees (USD)",
        margin=dict(l=80, r=40, t=80, b=40),
    )

    # ===== BUILD HTML =====
    pos = efficiency["position"]
    pos_blocks = efficiency["position_blocks"]
    vault_status = efficiency["vault_status"].upper()
    status_color = "#00CC96" if efficiency["vault_status"] == "active" else "#FFA15A"

    # Format efficiency ratio color
    eff_ratio = efficiency["efficiency_ratio"]
    eff_color = "#00CC96" if eff_ratio >= 1 else ("#FFA15A" if eff_ratio > 0 else "#888")

    html = [
        "<!DOCTYPE html><html><head>",
        '<meta charset="utf-8">',
        "<title>Chutes Capital Efficiency</title>",
        "<style>",
        "body { background: #111; color: #eee; font-family: -apple-system, BlinkMacSystemFont, sans-serif; padding: 20px 40px; max-width: 1400px; margin: 0 auto; }",
        "h1 { text-align: center; font-size: 28px; margin-bottom: 4px; }",
        ".subtitle { text-align: center; color: #888; font-size: 14px; margin-bottom: 32px; }",
        "h2 { margin-top: 48px; border-bottom: 1px solid #333; padding-bottom: 8px; font-size: 20px; }",
        ".cards { display: flex; gap: 16px; flex-wrap: wrap; margin: 20px 0; }",
        ".card { background: #1a1a1a; border-radius: 10px; padding: 20px; flex: 1; min-width: 200px; }",
        ".card h3 { margin: 0 0 12px 0; font-size: 14px; color: #888; text-transform: uppercase; letter-spacing: 1px; }",
        ".card .big { font-size: 32px; font-weight: 700; margin-bottom: 8px; }",
        ".card .detail { font-size: 13px; color: #aaa; line-height: 1.6; }",
        ".metric-cards { display: flex; gap: 16px; flex-wrap: wrap; margin: 20px 0; }",
        ".metric-card { background: #1a1a1a; border-radius: 10px; padding: 24px; flex: 1; min-width: 250px; text-align: center; }",
        ".metric-card .label { font-size: 13px; color: #888; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 8px; }",
        ".metric-card .value { font-size: 42px; font-weight: 700; }",
        ".metric-card .sub { font-size: 12px; color: #666; margin-top: 8px; }",
        "</style>",
        "</head><body>",
        "<h1>Chutes Capital Efficiency</h1>",
        f'<div class="subtitle">xSN64 / USDC on Base (Aerodrome CL Slipstream) &mdash; {cached_at}</div>',
    ]

    # --- Overview Cards ---
    html.append('<h2>Vault Overview</h2>')
    html.append('<div class="cards">')

    # Card 1: Status
    idle_text = ""
    if efficiency["vault_status"] == "idle":
        idle_text = f'<br>{vault_balances["usdc"]:.2f} USDC + {vault_balances["xsn64"]:.2f} xSN64 idle'
        idle_usd = vault_balances["usdc"] + vault_balances["xsn64"] * efficiency["current_price"]
        idle_text += f'<br>(~${idle_usd:,.2f} total)'
    html.append(
        f'<div class="card" style="border-top: 3px solid {status_color};">'
        f'<h3>Vault Status</h3>'
        f'<div class="big" style="color:{status_color};">{vault_status}</div>'
        f'<div class="detail">Protocol Vault (Vault 1){idle_text}</div>'
        f'</div>'
    )

    # Card 2: Position Summary
    if pos:
        duration_text = f'{pos_blocks["duration"]:,} blocks' if pos_blocks["duration"] else "N/A"
        html.append(
            f'<div class="card">'
            f'<h3>Last Position</h3>'
            f'<div class="big">{pos["width_pct"]:.0f}%</div>'
            f'<div class="detail">'
            f'Range: ${pos["price_lower"]:.2f} – ${pos["price_upper"]:.2f}<br>'
            f'Ticks: [{pos["tick_lower"]}, {pos["tick_upper"]}]<br>'
            f'Duration: {duration_text}'
            f'</div></div>'
        )
    else:
        html.append(
            '<div class="card"><h3>Position</h3>'
            '<div class="big" style="color:#888;">None</div>'
            '<div class="detail">No position history found</div></div>'
        )

    # Card 3: Capital Deployed
    if pos:
        html.append(
            f'<div class="card">'
            f'<h3>Capital Deployed</h3>'
            f'<div class="big">${pos["deposited_usd"]:.2f}</div>'
            f'<div class="detail">'
            f'{pos["deposited_usdc"]:.2f} USDC + {pos["deposited_xsn64"]:.4f} xSN64'
            f'</div></div>'
        )
    else:
        html.append(
            '<div class="card"><h3>Capital Deployed</h3>'
            '<div class="big">$0</div>'
            '<div class="detail">No position opened</div></div>'
        )

    # Card 4: Fees Earned
    vf = efficiency["vault_fees"]
    html.append(
        f'<div class="card" style="border-top: 3px solid {VAULT_COLOR};">'
        f'<h3>Fees Earned</h3>'
        f'<div class="big">${vf["usd"]:.4f}</div>'
        f'<div class="detail">'
        f'{vf["usdc"]:.6f} USDC + {vf["xsn64"]:.6f} xSN64'
        f'</div></div>'
    )

    html.append('</div>')

    # --- Efficiency Metric Cards ---
    html.append('<h2>Capital Efficiency</h2>')
    html.append('<div class="metric-cards">')

    html.append(
        f'<div class="metric-card">'
        f'<div class="label">TVL Share</div>'
        f'<div class="value" style="color:{VAULT_COLOR};">{efficiency["tvl_share_pct"]:.2f}%</div>'
        f'<div class="sub">${efficiency["deposit_usd"]:,.0f} deployed / ${efficiency["pool_tvl_usd"]:,.0f} pool TVL</div>'
        f'</div>'
    )

    html.append(
        f'<div class="metric-card">'
        f'<div class="label">Fee Share</div>'
        f'<div class="value" style="color:{POOL_COLOR};">{efficiency["fee_share_pct"]:.2f}%</div>'
        f'<div class="sub">Our fees / Total pool fees<br>(during position lifetime)</div>'
        f'</div>'
    )

    html.append(
        f'<div class="metric-card" style="border: 2px solid {eff_color};">'
        f'<div class="label">Efficiency Ratio</div>'
        f'<div class="value" style="color:{eff_color};">{eff_ratio:.2f}x</div>'
        f'<div class="sub">Fee Share / TVL Share<br>&gt;1.0 = outperforming pool average</div>'
        f'</div>'
    )

    html.append('</div>')

    # --- Charts ---
    html.append('<h2>Position Timeline</h2>')
    html.append('<p style="color:#888;font-size:13px;">Green band = our vault\'s active position range. White line = market price. Dashed lines = position open/close.</p>')
    html.append(fig_timeline.to_html(full_html=False, include_plotlyjs="cdn"))

    html.append('<h2>Fee Comparison</h2>')
    html.append('<p style="color:#888;font-size:13px;">Cumulative pool trading fees over time. Green shaded area = when our position was active. Green dotted line = our total fees earned.</p>')
    html.append(fig_fees.to_html(full_html=False, include_plotlyjs=False))

    # --- Pool Activity Summary ---
    html.append('<h2>Pool Activity</h2>')
    fee_tier = pool_state["fee_tier"]
    fee_pct = fee_tier / 10_000

    total_mints = len(pool_events.get("Mint", []))
    total_burns = len(pool_events.get("Burn", []))
    total_collects = len(pool_events.get("Collect", []))
    total_swaps = len(pool_events.get("Swap", []))
    vault_mints = len(vault_events.get("Mint", []))
    vault_burns = len(vault_events.get("Burn", []))
    vault_collects = len(vault_events.get("Collect", []))

    html.append(f"""
    <div class="cards">
      <div class="card">
        <h3>Pool Info</h3>
        <div class="detail">
          Fee tier: <b>{fee_pct:.2f}%</b> ({fee_tier}/1M)<br>
          Tick spacing: <b>{pool_state["tick_spacing"]}</b><br>
          Current price: <b>${efficiency["current_price"]:.2f}</b> USDC/xSN64<br>
          Current tick: <b>{efficiency["current_tick"]}</b><br>
          Pool TVL: <b>${efficiency["pool_tvl_usd"]:,.0f}</b> (DexScreener)
        </div>
      </div>
      <div class="card">
        <h3>Pool Events</h3>
        <div class="detail">
          Mints: <b>{total_mints}</b> (ours: {vault_mints})<br>
          Burns: <b>{total_burns}</b> (ours: {vault_burns})<br>
          Collects: <b>{total_collects}</b> (ours: {vault_collects})<br>
          Swaps: <b>{total_swaps}</b>
        </div>
      </div>
      <div class="card">
        <h3>Fee Totals</h3>
        <div class="detail">
          Pool fees (all time): <b>${efficiency["total_pool_fees_all_time_usd"]:.2f}</b><br>
          Pool fees (during position): <b>${efficiency["total_pool_fees_usd"]:.2f}</b><br>
          Our fees: <b>${efficiency["vault_fees"]["usd"]:.4f}</b><br>
          Vault transactions: <b>{sum(efficiency["tx_categories"].values())}</b>
        </div>
      </div>
    </div>
    """)

    # --- Methodology ---
    html.append("""
    <h2>How This Was Calculated</h2>
    <div style="color:#aaa; font-size:13px; line-height:1.8; max-width:900px;">

    <h3 style="color:#eee; font-size:16px; margin-top:24px;">Capital Efficiency Ratio</h3>
    <p>The core metric: <b>Efficiency Ratio = Fee Share / TVL Share</b>.
    A ratio &gt;1 means our vault earns a disproportionately large share of pool fees relative to the capital deployed.
    This happens when our liquidity is concentrated near the active price, capturing more fees per dollar than wider positions.</p>

    <h3 style="color:#eee; font-size:16px; margin-top:24px;">TVL Share</h3>
    <p>TVL Share = our deposit value (USD) / pool total TVL (USD).
    Our deposit is valued at the market price when the position was opened (from the nearest Swap event).
    Pool TVL is fetched from the DexScreener API.
    This dollar-based metric shows what fraction of pool capital is ours &mdash; the meaningful input for capital efficiency.
    (A liquidity-L-based ratio would just confirm V3 distributes fees proportionally to L, which is true by design.)</p>

    <h3 style="color:#eee; font-size:16px; margin-top:24px;">Fee Share</h3>
    <p>Fee Share = our fees / total pool fees (during our position's active period).
    Our fees come from Collect events: <code>fees = Collect amounts &minus; Burn amounts</code>
    (standard V3 fee extraction &mdash; <code>burn(0)</code> triggers fee accounting, <code>collect()</code> returns principal + fees).
    Total pool fees are derived from Swap events: <code>fee = input_amount &times; fee_tier / 1,000,000</code>.
    The Swap event emits gross amounts (fee included), so fee_tier is applied directly.</p>

    <h3 style="color:#eee; font-size:16px; margin-top:24px;">Data Source</h3>
    <p>All data comes from on-chain events on the xSN64/USDC Aerodrome CL pool on Base, fetched via RPC <code>eth_getLogs</code>.
    Vault transactions identified by matching Collect event <code>recipient</code> field to the vault address,
    supplemented by Blockscout API queries on the LiquidityManager contract.</p>
    <p><b>Data range:</b> """ + data_range_str + """</p>

    <h3 style="color:#eee; font-size:16px; margin-top:24px;">Limitations</h3>
    <ul>
      <li>Vault 1 had one short-lived position (~1,400 blocks). Metrics are based on limited data.</li>
      <li>Pool liquidity during position is averaged from Swap events in that window. Low swap count = less precise estimate.</li>
      <li>Fee calculation assumes standard Uniswap V3 / Aerodrome CL fee mechanics.</li>
    </ul>

    </div>
    """)

    html.append("</body></html>")

    output_path = OUTPUT_DIR / "chutes_dashboard.html"
    with open(output_path, "w") as f:
        f.write("\n".join(html))
    print(f"\nDashboard saved to: {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("Chutes Capital Efficiency Analysis")
    print("=" * 60)

    cached = load_cache()

    if cached and "vault_tx_hashes" in cached:
        pool_events = cached["events"]
        vault_tx_hashes = set(cached["vault_tx_hashes"])
        pool_state = cached.get("pool_state", {})
        vault_balances = cached.get("vault_balances", {"usdc": 0, "xsn64": 0})
        print("Using cached data.")
    else:
        # Step 1: Pool state
        print("\nQuerying pool state...")
        pool_state = get_pool_state()
        print(f"  Fee tier: {pool_state['fee_tier']} ({pool_state['fee_tier']/10_000:.2f}%)")
        print(f"  Current tick: {pool_state['tick']}")
        print(f"  Active liquidity: {int(pool_state['liquidity']):,}")

        # Step 2: Vault balances
        print("\nQuerying vault balances...")
        vault_balances = get_vault_balances()
        print(f"  {vault_balances['usdc']:.2f} USDC + {vault_balances['xsn64']:.4f} xSN64")

        # Step 3: Fetch pool events
        to_block = w3.eth.block_number
        print(f"\nFetching pool events from block {START_BLOCK} to {to_block}...")
        pool_events = fetch_all_pool_events(START_BLOCK, to_block)

        # Step 4: Discover vault tx hashes
        print("\nDiscovering vault tx hashes...")
        vault_tx_hashes = discover_vault_tx_hashes(pool_events.get("Collect", []))

        # Step 5: Cache
        save_cache(pool_events, vault_tx_hashes, pool_state, vault_balances)

    # Match events to vault
    print("\nMatching events to vault...")
    vault_events = match_vault_events(pool_events, vault_tx_hashes)

    # Compute swap fees
    print("\nComputing swap fees...")
    fee_tier = pool_state.get("fee_tier", 10000)
    swap_fees_df = compute_swap_fees(pool_events.get("Swap", []), fee_tier)
    if not swap_fees_df.empty:
        print(f"  {len(swap_fees_df)} swaps, ${swap_fees_df['fee_usd'].sum():.2f} total fees")

    # Capital efficiency
    print("\nComputing capital efficiency...")
    efficiency = compute_capital_efficiency(
        vault_events, swap_fees_df, pool_state, pool_events.get("Swap", [])
    )

    # Build timelines
    print("\nBuilding timelines...")
    timeline = build_position_timeline(vault_events)
    segments = build_range_segments(timeline)
    price_df = build_price_timeline(pool_events.get("Swap", []))
    print(f"  {len(timeline)} timeline events, {len(segments)} segments, {len(price_df)} price points")

    # Block timestamps
    all_blocks = set()
    if not timeline.empty:
        all_blocks.update(timeline["block"].tolist())
    if not price_df.empty:
        all_blocks.update(price_df["block"].tolist())
    if not swap_fees_df.empty:
        all_blocks.update(swap_fees_df["block"].tolist())

    print(f"\nBuilding timestamp map for {len(all_blocks)} blocks...")
    block_ts = build_block_timestamps(list(all_blocks))

    # Snapshot timestamp
    if cached:
        snapshot_ts = cached.get("cached_at", "")
        try:
            dt = datetime.fromisoformat(snapshot_ts)
            snapshot_str = dt.strftime("%b %d, %Y at %H:%M UTC")
        except Exception:
            snapshot_str = datetime.now(tz=timezone.utc).strftime("%b %d, %Y at %H:%M UTC")
    else:
        snapshot_str = datetime.now(tz=timezone.utc).strftime("%b %d, %Y at %H:%M UTC")

    # Dashboard
    print("\nGenerating dashboard...")
    path = build_dashboard(
        efficiency, vault_events, pool_events, swap_fees_df,
        price_df, segments, block_ts, pool_state, vault_balances,
        cached_at=snapshot_str,
    )

    # Print summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Vault status:          {efficiency['vault_status']}")
    print(f"  Current price:         ${efficiency['current_price']:.2f} USDC/xSN64")
    print(f"  Idle balances:         {vault_balances['usdc']:.2f} USDC + {vault_balances['xsn64']:.4f} xSN64")
    if efficiency["position"]:
        p = efficiency["position"]
        print(f"  Position range:        ${p['price_lower']:.2f} – ${p['price_upper']:.2f} ({p['width_pct']:.0f}%)")
        print(f"  Capital deployed:      ${p['deposited_usd']:.2f}")
    print(f"  Vault fees:            ${efficiency['vault_fees']['usd']:.4f} ({efficiency['vault_fees']['usdc']:.6f} USDC + {efficiency['vault_fees']['xsn64']:.6f} xSN64)")
    print(f"  Pool fees (position):  ${efficiency['total_pool_fees_usd']:.2f}")
    print(f"  Pool fees (all time):  ${efficiency['total_pool_fees_all_time_usd']:.2f}")
    print(f"  Pool TVL:              ${efficiency['pool_tvl_usd']:,.0f}")
    print(f"  TVL share:             {efficiency['tvl_share_pct']:.2f}% (${efficiency['deposit_usd']:,.0f} / ${efficiency['pool_tvl_usd']:,.0f})")
    print(f"  Fee share:             {efficiency['fee_share_pct']:.4f}%")
    print(f"  Efficiency ratio:      {efficiency['efficiency_ratio']:.4f}x")
    print(f"\nDone! Open {path} in a browser.")


if __name__ == "__main__":
    main()

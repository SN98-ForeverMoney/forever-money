"""Generate a config YAML for every pool in pools_inventory.csv.

For each pool:
  1. Fetch metadata (token addresses, decimals, fee_tier, tick_spacing) via RPC
  2. Query DB for first swap's sqrt_price_x96 at the earliest usable start_block
  3. Derive USD price of both tokens via price_oracle
  4. Compute position_size_token1_raw = $10K / token1_usd_price × 10^decimals1
  5. Write a config YAML per pool

Pools with <MIN_SWAPS total are skipped.
"""
from __future__ import annotations

import asyncio
import csv
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv
from tortoise import Tortoise
from web3 import Web3

from backtester_pullout.backtester.db import init_db, close_db
from backtester_pullout.backtester.price_oracle import (
    KNOWN_TOKEN_USD_PRICE, derive_pool_prices,
    position_size_token1_raw_for_usd,
)
from backtester_pullout.scripts.prepare_pool_config import fetch_pool_metadata


END_BLOCK = 44_042_900
PERIOD_DAYS_TO_BLOCKS = {
    30: 1_295_995,
    60: 2_591_999,
    90: 3_887_986,
    120: 5_184_000,
}
MIN_SWAPS = 1000   # skip pools with fewer total swaps
POSITION_USD = 10_000
TX_COST_USD = 0.01


STRATEGY_PARAMS = {
    "width_factor": 3.0,
    "volatility_window": 10,
    "min_width_spacings": 25,
    "edge_buffer_pct": 0.20,
    "dust_threshold": 10000,
    "exit_vol_threshold": 0.003,
    "reentry_vol_threshold": 0.0015,
}


async def get_first_sqrt_price_at_or_after(conn, addr: str, block: int) -> int:
    rows = await conn.execute_query_dict(
        "SELECT sqrt_price_x96 FROM base_poocl_swaps_v2 "
        "WHERE evt_address = $1 AND evt_block_number >= $2 "
        "ORDER BY evt_block_number ASC LIMIT 1",
        [addr, block],
    )
    if not rows:
        raise RuntimeError(f"no swaps for {addr} at block >= {block}")
    return int(rows[0]["sqrt_price_x96"])


async def main() -> None:
    load_dotenv()
    rpc = os.environ.get("BASE_RPC")
    if not rpc:
        print("BASE_RPC not set", file=sys.stderr); return
    w3 = Web3(Web3.HTTPProvider(rpc))

    inv_path = Path("backtester_pullout/config/pools_inventory.csv")
    if not inv_path.exists():
        print(f"Run classify_pools.py first to generate {inv_path}", file=sys.stderr); return

    out_dir = Path("backtester_pullout/config/generated")
    out_dir.mkdir(parents=True, exist_ok=True)

    with inv_path.open() as f:
        pools = list(csv.DictReader(f))

    await init_db()
    try:
        conn = Tortoise.get_connection("default")
        summary = []
        for p in pools:
            addr = p["address"]
            n = int(p["swap_count"])
            if n < MIN_SWAPS:
                print(f"SKIP {addr}: {n} swaps < {MIN_SWAPS}")
                continue
            try:
                meta = fetch_pool_metadata(w3, addr)
                t0 = meta["token0"]
                t1 = meta["token1"]
                dec0 = meta["decimals0"]
                dec1 = meta["decimals1"]
                sym0 = KNOWN_TOKEN_USD_PRICE.get(Web3.to_checksum_address(t0),
                                                  (meta["_symbol0"], None, None))[0]
                sym1 = KNOWN_TOKEN_USD_PRICE.get(Web3.to_checksum_address(t1),
                                                  (meta["_symbol1"], None, None))[0]

                # Use 120d start as the reference block (earliest we'd need)
                start_block = END_BLOCK - PERIOD_DAYS_TO_BLOCKS[120]
                addr_no0x = addr.lower().removeprefix("0x")
                sqrt_p = await get_first_sqrt_price_at_or_after(conn, addr_no0x, start_block)

                prices = derive_pool_prices(
                    sqrt_p, dec0, dec1,
                    Web3.to_checksum_address(t0),
                    Web3.to_checksum_address(t1),
                )
                pos_raw = position_size_token1_raw_for_usd(
                    POSITION_USD, prices.token1_usd, dec1,
                )
                tx_raw = max(1, position_size_token1_raw_for_usd(
                    TX_COST_USD, prices.token1_usd, dec1,
                ))

                cfg = {
                    "pools": [{
                        "address": addr,
                        "symbol": f"{sym0}/{sym1}",
                        "token0": t0, "token1": t1,
                        "decimals0": dec0, "decimals1": dec1,
                        "fee_tier": meta["fee_tier"],
                        "tick_spacing": meta["tick_spacing"],
                        "range": {"type": "tick_width", "width_ticks": 20 * meta["tick_spacing"]},
                        "position_size_usd": POSITION_USD,
                        "position_size_token1_raw": pos_raw,
                        "tx_cost_usd": TX_COST_USD,
                        "tx_cost_token1_raw": tx_raw,
                        "slippage_bps": 2,
                        "action_delay_blocks": 15,
                    }],
                    "backtest": {"start_block": start_block, "end_block": END_BLOCK},
                    "prediction": {"horizon_blocks": 300, "noise_sigma": 0.2, "vol_bucket_blocks": 30},
                    "strategy": {"type": "volatility_miner", "params": STRATEGY_PARAMS},
                    "seed": 42,
                }

                fname = f"pool_{addr[2:10]}.yaml"
                (out_dir / fname).write_text(yaml.safe_dump(cfg, sort_keys=False))
                print(f"  {addr[:12]}... {sym0}/{sym1:10s} "
                      f"t0=${prices.token0_usd:.4g} t1=${prices.token1_usd:.4g} "
                      f"pos_raw={pos_raw} → {fname}")
                summary.append({
                    "address": addr, "symbol": f"{sym0}/{sym1}",
                    "swap_count": n, "fee_tier": meta["fee_tier"],
                    "token0_usd": prices.token0_usd, "token1_usd": prices.token1_usd,
                    "position_size_token1_raw": pos_raw,
                    "config": fname,
                })
            except Exception as e:
                print(f"FAIL {addr}: {e}")

        # Write a summary CSV
        sum_path = out_dir / "_summary.csv"
        with sum_path.open("w", newline="") as f:
            if summary:
                w = csv.DictWriter(f, fieldnames=list(summary[0].keys()))
                w.writeheader()
                w.writerows(summary)
        print(f"\nGenerated {len(summary)} configs. Summary at {sum_path}")
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())

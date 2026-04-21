"""Fetch metadata for all pools with >0 swaps and classify by token types.

Output: CSV with (address, symbol, token0, token1, dec0, dec1, fee, spacing,
swap_count, viable) — viable = True if both tokens are known (stable/WETH/cbBTC).
"""
from __future__ import annotations

import asyncio
import csv
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from tortoise import Tortoise
from web3 import Web3

from backtester_pullout.backtester.db import init_db, close_db
from backtester_pullout.scripts.prepare_pool_config import fetch_pool_metadata


# Known token prices in USD for position-sizing (rough)
KNOWN_TOKEN_USD = {
    "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913": ("USDC", 1.0),
    "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb": ("DAI", 1.0),
    "0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA": ("USDbC", 1.0),
    "0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2": ("USDT", 1.0),
    "0x4200000000000000000000000000000000000006": ("WETH", 3000.0),
    "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf": ("cbBTC", 100000.0),
    "0xA88594D404727625A9437C3f886C7643872296AE": ("WELL", 0.05),
    "0xa1832f7F4e534aE557f9B5AB76dE54B1873e498B": ("BID", None),
    "0xb99FBE68c8A0cC14bE8c1AF73DD4DfEA8a76aDD7": ("xTAO", None),
}


async def main() -> None:
    load_dotenv()
    rpc = os.environ.get("BASE_RPC")
    if not rpc:
        print("BASE_RPC not set", file=sys.stderr)
        return
    w3 = Web3(Web3.HTTPProvider(rpc))

    await init_db()
    try:
        conn = Tortoise.get_connection("default")
        rows = await conn.execute_query_dict(
            "SELECT evt_address, COUNT(*) AS n FROM base_poocl_swaps_v2 "
            "GROUP BY evt_address ORDER BY n DESC"
        )
    finally:
        await close_db()

    out_path = Path("backtester_pullout/config/pools_inventory.csv")
    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "address", "swap_count", "symbol", "token0", "token1",
            "dec0", "dec1", "fee_tier", "tick_spacing",
            "token0_known", "token1_known", "viable",
        ])
        for r in rows:
            addr = "0x" + r["evt_address"]
            n = r["n"]
            try:
                meta = fetch_pool_metadata(w3, addr)
                t0_addr = Web3.to_checksum_address(meta["token0"])
                t1_addr = Web3.to_checksum_address(meta["token1"])
                t0_known = t0_addr in KNOWN_TOKEN_USD
                t1_known = t1_addr in KNOWN_TOKEN_USD
                sym0 = KNOWN_TOKEN_USD.get(t0_addr, (meta.get("_symbol0", "?"), None))[0]
                sym1 = KNOWN_TOKEN_USD.get(t1_addr, (meta.get("_symbol1", "?"), None))[0]
                symbol = f"{sym0}/{sym1}"
                viable = t0_known and t1_known
                w.writerow([
                    addr, n, symbol, meta["token0"], meta["token1"],
                    meta["decimals0"], meta["decimals1"],
                    meta["fee_tier"], meta["tick_spacing"],
                    t0_known, t1_known, viable,
                ])
                print(f"  {addr}: {n:>10,} {symbol:20s} "
                      f"fee={meta['fee_tier']} ts={meta['tick_spacing']} "
                      f"viable={viable}")
            except Exception as e:
                print(f"  {addr}: {n:>10,}  ERR: {str(e)[:80]}")
                w.writerow([addr, n, "ERR", "", "", "", "", "", "", False, False, False])

    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    asyncio.run(main())

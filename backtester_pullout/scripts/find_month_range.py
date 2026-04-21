"""Find the block range covering the last N days for a given pool.

Uses evt_block_time (unix seconds) on the swap table.
"""
from __future__ import annotations

import asyncio
import sys

from tortoise import Tortoise

from backtester_pullout.backtester.db import init_db, close_db


def normalize_addr(addr: str) -> str:
    return addr.lower().removeprefix("0x")


async def main(pool: str, days: int) -> None:
    await init_db()
    try:
        addr = normalize_addr(pool)
        conn = Tortoise.get_connection("default")
        rows = await conn.execute_query_dict(
            "SELECT MIN(evt_block_number) AS min_b, MAX(evt_block_number) AS max_b, "
            "MIN(evt_block_time) AS min_t, MAX(evt_block_time) AS max_t, "
            "COUNT(*) AS n "
            "FROM base_poocl_swaps_v2 WHERE evt_address = $1",
            [addr],
        )
        r = rows[0]
        print(f"Pool {addr} has {r['n']:,} swaps total.")
        print(f"Block range : {r['min_b']} → {r['max_b']}")
        print(f"Time  range : {r['min_t']} → {r['max_t']}")

        if r["max_t"] is None:
            print("No data with non-null evt_block_time.")
            return

        cutoff = int(r["max_t"]) - days * 24 * 3600
        # evt_block_time is text in the DB — compare as text (inline cutoff int)
        rows = await conn.execute_query_dict(
            f"SELECT MIN(evt_block_number) AS b FROM base_poocl_swaps_v2 "
            f"WHERE evt_address = $1 AND evt_block_time::bigint >= {cutoff}",
            [addr],
        )
        start_block = rows[0]["b"]
        print(f"\nLast {days} days (since ts={cutoff}):")
        print(f"  start_block = {start_block}")
        print(f"  end_block   = {r['max_b']}")
        print(f"  span        = {r['max_b'] - start_block:,} blocks")

        rows = await conn.execute_query_dict(
            f"SELECT COUNT(*) AS n FROM base_poocl_swaps_v2 "
            f"WHERE evt_address = $1 AND evt_block_number >= {start_block} "
            f"AND evt_block_number <= {r['max_b']}",
            [addr],
        )
        print(f"  swaps       = {rows[0]['n']:,}")
    finally:
        await close_db()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: find_month_range.py <pool_addr> <days>", file=sys.stderr)
        sys.exit(2)
    asyncio.run(main(sys.argv[1], int(sys.argv[2])))

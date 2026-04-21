"""List distinct pool addresses from both swap tables with counts + date ranges."""
from __future__ import annotations

import asyncio

from tortoise import Tortoise

from backtester_pullout.backtester.db import init_db, close_db


async def main() -> None:
    await init_db()
    try:
        conn = Tortoise.get_connection("default")
        for table in ["base_poocl_swaps_v2", "base_univ3_swaps_v2"]:
            print(f"=== {table} ===")
            rows = await conn.execute_query_dict(
                f"SELECT evt_address, COUNT(*) AS n, "
                f"MIN(evt_block_number) AS min_b, MAX(evt_block_number) AS max_b "
                f"FROM {table} GROUP BY evt_address ORDER BY n DESC"
            )
            for r in rows:
                print(f"  0x{r['evt_address']}: {r['n']:>12,} swaps  "
                      f"blocks {r['min_b']} → {r['max_b']}")
            print()

        # Check overlap
        print("=== Pool overlap check ===")
        rows = await conn.execute_query_dict(
            "SELECT a.evt_address, a.n AS poocl_n, b.n AS univ3_n "
            "FROM (SELECT evt_address, COUNT(*) AS n FROM base_poocl_swaps_v2 GROUP BY 1) a "
            "FULL OUTER JOIN (SELECT evt_address, COUNT(*) AS n FROM base_univ3_swaps_v2 GROUP BY 1) b "
            "USING (evt_address) "
            "ORDER BY COALESCE(a.n, b.n) DESC"
        )
        print(f"  {'address':<45} {'poocl':>12} {'univ3':>12}")
        for r in rows:
            p = r.get('poocl_n') or '-'
            u = r.get('univ3_n') or '-'
            if isinstance(p, int):
                p = f"{p:,}"
            if isinstance(u, int):
                u = f"{u:,}"
            print(f"  0x{r['evt_address']:<43} {p:>12} {u:>12}")
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())

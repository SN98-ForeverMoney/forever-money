"""Peek at the pool_event tables to see what pools/ranges are populated."""
from __future__ import annotations

import asyncio

from backtester_pullout.backtester.db import init_db, close_db
from backtester_pullout.backtester.models import SwapEvent


async def main() -> None:
    await init_db()
    try:
        total = await SwapEvent.all().count()
        print(f"Total swaps: {total:,}")

        sample = await SwapEvent.all().limit(3)
        for s in sample:
            print(
                f"  id={s.id} addr={s.evt_address} block={s.evt_block_number} "
                f"time={s.evt_block_time} tick={s.tick}"
            )

        # Top pools by swap count (approximation: just sample distinct addresses)
        from tortoise import Tortoise
        conn = Tortoise.get_connection("default")
        rows = await conn.execute_query_dict(
            "SELECT evt_address, COUNT(*) AS n, MIN(evt_block_number) AS min_b, "
            "MAX(evt_block_number) AS max_b "
            "FROM base_poocl_swaps_v2 GROUP BY evt_address ORDER BY n DESC LIMIT 10"
        )
        print("\nTop pools by swap count:")
        for r in rows:
            print(
                f"  {r['evt_address']}: {r['n']:>10,} swaps "
                f"blocks {r['min_b']} → {r['max_b']}"
            )
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())

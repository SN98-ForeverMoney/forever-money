"""Check mints/burns/collects tables."""
from __future__ import annotations

import asyncio

from tortoise import Tortoise

from backtester_pullout.backtester.db import init_db, close_db


async def main() -> None:
    await init_db()
    try:
        conn = Tortoise.get_connection("default")
        for table in ["base_poolcl_mints_v2", "base_poolcl_burns_v2", "base_poolcl_collects_v2"]:
            rows = await conn.execute_query_dict(f"SELECT COUNT(*) AS n FROM {table}")
            print(f"{table}: {rows[0]['n']:,}")

            rows = await conn.execute_query_dict(
                f"SELECT evt_address, COUNT(*) AS n FROM {table} "
                f"GROUP BY evt_address ORDER BY n DESC LIMIT 5"
            )
            for r in rows:
                print(f"    {r['evt_address']}: {r['n']:,}")
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())

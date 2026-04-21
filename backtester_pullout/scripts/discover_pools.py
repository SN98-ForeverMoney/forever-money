"""Discover all swap-related tables and pool addresses in the DB.

For each table with 'swap' in the name, count distinct evt_address and top pools.
"""
from __future__ import annotations

import asyncio

from tortoise import Tortoise

from backtester_pullout.backtester.db import init_db, close_db


async def main() -> None:
    await init_db()
    try:
        conn = Tortoise.get_connection("default")
        # All tables with 'swap' in the name
        rows = await conn.execute_query_dict(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_name ILIKE '%swap%' "
            "ORDER BY table_schema, table_name"
        )
        print(f"{'schema':<15} {'table':<55} {'total':>12} {'pools':>6}")
        print("-" * 92)
        for r in rows:
            schema, name = r["table_schema"], r["table_name"]
            try:
                cnt = await conn.execute_query_dict(
                    f'SELECT COUNT(*) AS n FROM "{schema}"."{name}"'
                )
                n = cnt[0]["n"]
                # Distinct pools
                pools = await conn.execute_query_dict(
                    f'SELECT COUNT(DISTINCT evt_address) AS p FROM "{schema}"."{name}"'
                )
                p = pools[0]["p"]
                print(f"{schema:<15} {name:<55} {n:>12,} {p:>6,}")
            except Exception as e:
                print(f"{schema:<15} {name:<55} ERR: {str(e)[:40]}")
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())

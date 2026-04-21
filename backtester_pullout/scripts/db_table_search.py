"""Search the DB for any tables matching mint/burn/collect/initialize patterns."""
from __future__ import annotations

import asyncio

from tortoise import Tortoise

from backtester_pullout.backtester.db import init_db, close_db


async def main() -> None:
    await init_db()
    try:
        conn = Tortoise.get_connection("default")
        rows = await conn.execute_query_dict(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_name ILIKE ANY (ARRAY['%mint%', '%burn%', '%collect%', "
            "'%initialize%', '%pool%cl%', '%pool_cl%']) "
            "ORDER BY table_schema, table_name"
        )
        print(f"{'schema':<20} {'table':<60} {'rows (est)':>12}")
        print("-" * 95)
        for r in rows:
            schema, name = r["table_schema"], r["table_name"]
            try:
                cnt = await conn.execute_query_dict(
                    f'SELECT COUNT(*) AS n FROM "{schema}"."{name}"'
                )
                n = cnt[0]["n"]
            except Exception as e:
                n = f"ERR: {e}"
            print(f"{schema:<20} {name:<60} {n:>12}")
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())

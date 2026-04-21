"""Check which event tables have data for a specific pool."""
from __future__ import annotations
import asyncio
import sys

from tortoise import Tortoise
from backtester_pullout.backtester.db import init_db, close_db


async def main(pool: str) -> None:
    addr = pool.lower().removeprefix("0x")
    await init_db()
    try:
        conn = Tortoise.get_connection("default")
        for table in [
            "base_poocl_swaps_v2",
            "base_poocl_mints_v2",
            "base_univ3_mints_v2",
            "base_univ3_burns_v2",
            "base_univ3_collects_v2",
            "base_univ3_initializes_v2",
        ]:
            try:
                rows = await conn.execute_query_dict(
                    f"SELECT COUNT(*) AS n FROM {table} WHERE evt_address = $1",
                    [addr],
                )
                print(f"  {table:<35} {rows[0]['n']:>12,}")
            except Exception as e:
                print(f"  {table:<35} ERR: {e}")
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1
                     else "0xd0b53D9277642d899DF5C87A3966A349A798F224"))

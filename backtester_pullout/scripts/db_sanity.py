"""Quick DB sanity: count events for a pool address in a block range.

Usage:
  python -m backtester_pullout.scripts.db_sanity <pool_addr> <start_block> <end_block>

Note: pool address in the DB is stored WITHOUT 0x prefix.
"""
from __future__ import annotations

import asyncio
import sys

from backtester_pullout.backtester.db import init_db, close_db
from backtester_pullout.backtester.models import (
    SwapEvent, MintEvent, BurnEvent, CollectEvent,
)


def normalize_addr(addr: str) -> str:
    return addr.lower().removeprefix("0x")


async def main(pool: str, start: int, end: int) -> None:
    await init_db()
    try:
        addr = normalize_addr(pool)
        for label, model in [
            ("swaps", SwapEvent),
            ("mints", MintEvent),
            ("burns", BurnEvent),
            ("collects", CollectEvent),
        ]:
            n = await model.filter(
                evt_address=addr,
                evt_block_number__gte=start,
                evt_block_number__lte=end,
            ).count()
            print(f"  {label:9s}: {n:>10,}")

        first_swap = await SwapEvent.filter(
            evt_address=addr,
            evt_block_number__gte=start,
            evt_block_number__lte=end,
        ).order_by("evt_block_number").first()
        last_swap = await SwapEvent.filter(
            evt_address=addr,
            evt_block_number__gte=start,
            evt_block_number__lte=end,
        ).order_by("-evt_block_number").first()
        if first_swap and last_swap:
            print(
                f"  block range covered: {first_swap.evt_block_number} → "
                f"{last_swap.evt_block_number}"
            )
            print(
                f"  time range:           {first_swap.evt_block_time} → "
                f"{last_swap.evt_block_time}"
            )
            print(
                f"  first swap tick={first_swap.tick}, "
                f"sqrt_price_x96={first_swap.sqrt_price_x96}"
            )
    finally:
        await close_db()


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    pool, start, end = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    print(f"Counting events for pool {pool} in blocks [{start}, {end}]:")
    asyncio.run(main(pool, start, end))

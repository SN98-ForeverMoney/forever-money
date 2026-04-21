"""End-to-end smoke test: load real swaps, run engine with always_in strategy,
print final equity for all three benchmarks.

Usage:
    BACKTESTER_DB_URL=... python -m backtester_pullout.scripts.smoke_run \
        backtester_pullout/config/example.generated.yaml
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from backtester_pullout.backtester.config import load_config
from backtester_pullout.backtester.data import load_swaps
from backtester_pullout.backtester.engine import run_backtest
from backtester_pullout.backtester.strategies import build_strategy, register_builtins

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


async def main(config_path: str) -> None:
    cfg = load_config(config_path)
    pool = cfg.pools[0]

    print(f"Loading swaps for {pool.symbol} ({pool.address}) "
          f"blocks [{cfg.backtest.start_block}, {cfg.backtest.end_block}]...")
    swaps = await load_swaps(pool.address, cfg.backtest.start_block, cfg.backtest.end_block)
    print(f"Loaded {len(swaps):,} swaps.")
    if len(swaps) == 0:
        print("No swaps — aborting.")
        return

    register_builtins()
    strat = build_strategy("always_in")

    res = run_backtest(swaps, pool, cfg.prediction, strat, log_every=20_000)

    scale = 10 ** pool.decimals1
    print(f"\n=== Results (token1={pool.symbol.split('/')[-1]} human units) ===")
    print(f"  Initial hodl    : {res.equity['hodl'].iloc[0] / scale:>15,.4f}")
    print(f"  Final   hodl    : {res.equity['hodl'].iloc[-1] / scale:>15,.4f}")
    print(f"  Initial passive : {res.equity['passive'].iloc[0] / scale:>15,.4f}")
    print(f"  Final   passive : {res.equity['passive'].iloc[-1] / scale:>15,.4f}")
    print(f"  Initial strat   : {res.equity['strategy'].iloc[0] / scale:>15,.4f}")
    print(f"  Final   strat   : {res.equity['strategy'].iloc[-1] / scale:>15,.4f}")
    print(f"  Passive fees    : token0={res.final_passive.position.fees0 if res.final_passive.position else 0}, "
          f"token1={res.final_passive.position.fees1 if res.final_passive.position else 0}")
    print(f"  In-range swaps  : {res.final_passive.in_range_swap_count} / {res.final_passive.observed_swap_count}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    asyncio.run(main(sys.argv[1]))

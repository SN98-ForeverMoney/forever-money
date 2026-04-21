"""Single-run entrypoint: load config, fetch swaps, run engine, write outputs.

Usage:
    BACKTESTER_DB_URL=... python -m backtester_pullout.scripts.run_backtest \
        backtester_pullout/config/example.generated.yaml
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from backtester_pullout.backtester.config import load_config
from backtester_pullout.backtester.data import load_liq_events, load_liq_events_upto, load_swaps
from backtester_pullout.backtester.liquidity_map import LiquidityMap
from backtester_pullout.backtester.engine import run_backtest
from backtester_pullout.backtester.io import (
    make_results_dir,
    write_actions_csv,
    write_equity_csv,
    write_manifest,
    write_metrics_json,
)
from backtester_pullout.backtester.metrics import (
    compute_all_metrics,
    excess_return_vs_hodl,
)
from backtester_pullout.backtester.plots import equity_curve
from backtester_pullout.backtester.report import write_report
from backtester_pullout.backtester.strategies import build_strategy, register_builtins


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("run_backtest")


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("config", type=Path)
    ap.add_argument("--label", default="single", help="dir label suffix")
    ap.add_argument("--results", default="results", help="results base dir")
    ap.add_argument("--no-liq-map", action="store_true",
                    help="skip mint/burn reconstruction; use raw swap.liquidity field")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    pool = cfg.pools[0]

    logger.info(f"Loading swaps for {pool.symbol} blocks "
                f"[{cfg.backtest.start_block}, {cfg.backtest.end_block}]...")
    swaps = await load_swaps(pool.address, cfg.backtest.start_block, cfg.backtest.end_block)
    if len(swaps) == 0:
        logger.error("No swaps in range — aborting.")
        return 1
    logger.info(f"Loaded {len(swaps):,} swaps.")

    register_builtins()
    strategy = build_strategy(cfg.strategy.type, cfg.strategy.params)

    liq_map = None
    liq_events_in_window = None
    if not args.no_liq_map:
        logger.info(f"Seeding LiquidityMap from mints/burns up to block {cfg.backtest.start_block - 1}...")
        seed_events = await load_liq_events_upto(pool.address, cfg.backtest.start_block - 1)
        liq_map = LiquidityMap()
        liq_map.apply(seed_events)
        logger.info(f"  seeded {len(seed_events):,} events; "
                    f"{liq_map.num_ticks_with_liquidity():,} ticks with liquidity")
        logger.info(f"Loading mint/burn events in window...")
        liq_events_in_window = await load_liq_events(
            pool.address, cfg.backtest.start_block, cfg.backtest.end_block,
        )
        logger.info(f"  {len(liq_events_in_window):,} events in window")

    res = run_backtest(swaps, pool, cfg.prediction, strategy,
                       seed=cfg.seed, cell_index=0, log_every=50_000,
                       liq_map=liq_map, liq_events_in_window=liq_events_in_window)

    metrics = compute_all_metrics(res, decimals1=pool.decimals1)

    out = make_results_dir(args.results, args.label)
    write_equity_csv(res, out, decimals1=pool.decimals1)
    write_actions_csv(res, out)
    write_metrics_json(metrics, out)
    equity_curve(res, pool, out / "equity.png", strategy_params=cfg.strategy.params)
    write_manifest({
        "config_path": str(args.config),
        "strategy": {"type": cfg.strategy.type, "params": cfg.strategy.params},
        "prediction": cfg.prediction.model_dump(),
        "seed": cfg.seed,
        "cell_index": 0,
        "pool": pool.model_dump(),
        "backtest": cfg.backtest.model_dump(),
        "excess_return_vs_hodl": excess_return_vs_hodl(metrics),
    }, out)
    report_path = write_report(out)

    # Headline
    # Use logger.info so output flushes immediately even when run in background.
    logger.info("=== Metrics ===")
    for leg, m in metrics.items():
        logger.info(f"  [{leg}]")
        for k, v in m.items():
            logger.info(f"    {k:15s}: {v:,.6f}")
    logger.info(f"  excess_return_vs_hodl (strategy − hodl) = "
                f"{excess_return_vs_hodl(metrics):.6%}")
    logger.info(f"Outputs: {out}")
    logger.info(f"Report:  {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

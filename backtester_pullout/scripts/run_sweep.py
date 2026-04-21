"""Sweep entrypoint.

Usage:
    BACKTESTER_DB_URL=... python -m backtester_pullout.scripts.run_sweep \
        backtester_pullout/config/example.generated.yaml
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from backtester_pullout.backtester.config import load_config
from backtester_pullout.backtester.data import load_swaps
from backtester_pullout.backtester.io import make_results_dir, write_manifest
from backtester_pullout.backtester.plots import breakeven_curve, heatmap
from backtester_pullout.backtester.report import write_report
from backtester_pullout.backtester.strategies import register_builtins
from backtester_pullout.backtester.sweep import run_sweep


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("run_sweep")


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("config", type=Path)
    ap.add_argument("--label", default="sweep")
    ap.add_argument("--results", default="results")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    if cfg.sweep is None or not cfg.sweep.grid:
        logger.error("No sweep.grid in config.")
        return 1

    register_builtins()
    pool = cfg.pools[0]

    logger.info(f"Loading swaps once for {pool.symbol} blocks "
                f"[{cfg.backtest.start_block}, {cfg.backtest.end_block}]...")
    swaps = await load_swaps(pool.address, cfg.backtest.start_block, cfg.backtest.end_block)
    if len(swaps) == 0:
        logger.error("No swaps in range — aborting.")
        return 1
    logger.info(f"Loaded {len(swaps):,} swaps.")

    df, cells = run_sweep(cfg, swaps)

    out = make_results_dir(args.results, args.label)
    df.to_csv(out / "sweep.csv", index=False)
    with (out / "cells.json").open("w") as f:
        json.dump([{
            "cell_index": c.cell_index,
            "seed": c.seed,
            "overrides": c.overrides,
            "excess_return_vs_hodl": c.excess_return_vs_hodl,
        } for c in cells], f, indent=2, default=float)

    # Plots: heatmaps on axis pairs from the grid
    grid_keys = list(cfg.sweep.grid.keys())
    for i in range(len(grid_keys)):
        for j in range(i + 1, len(grid_keys)):
            x, y = grid_keys[i], grid_keys[j]
            png = out / f"heatmap_{x.replace('.', '_')}_vs_{y.replace('.', '_')}.png"
            heatmap(df, x, y, "excess_return_vs_hodl", png,
                    title=f"excess_return vs {x} × {y}")

    # Breakeven curve on noise_sigma if present, grouped by horizon
    noise_key = "prediction.noise_sigma"
    horizon_key = "prediction.horizon_blocks"
    if noise_key in df.columns and horizon_key in df.columns:
        breakeven_curve(df, noise_key, "excess_return_vs_hodl", horizon_key,
                        out / "breakeven.png",
                        title=f"{noise_key} break-even (by {horizon_key})")

    write_manifest({
        "config_path": str(args.config),
        "base_seed": cfg.seed,
        "num_cells": len(cells),
        "grid_keys": grid_keys,
        "pool": pool.model_dump(),
        "backtest": cfg.backtest.model_dump(),
    }, out)

    report_path = write_report(out)

    print(f"\nSweep done. {len(cells)} cells → {out}")
    print(f"Report:     {report_path}")
    print(f"Best cell: excess_return_vs_hodl = {df['excess_return_vs_hodl'].max():.6%}")
    print(df.nlargest(5, "excess_return_vs_hodl")[
        ["cell_index", "excess_return_vs_hodl"] + grid_keys
    ].to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

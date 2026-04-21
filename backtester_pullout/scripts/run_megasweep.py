"""Megasweep: (period × noise × slippage) cartesian product.

Per-period: load swaps + mint/burn events ONCE, build base LiquidityMap by
replaying seed events, then run each (noise, slippage) cell by copying the
seeded map.

Saves `megasweep.csv` incrementally after each cell → safe to interrupt.

Usage:
    BACKTESTER_DB_URL=... python -m backtester_pullout.scripts.run_megasweep
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from backtester_pullout.backtester.config import load_config
from backtester_pullout.backtester.data import (
    load_liq_events,
    load_liq_events_upto,
    load_swaps,
)
from backtester_pullout.backtester.engine import run_backtest
from backtester_pullout.backtester.liquidity_map import LiquidityMap
from backtester_pullout.backtester.metrics import (
    compute_all_metrics,
    excess_return_vs_hodl,
)
from backtester_pullout.backtester.strategies import build_strategy, register_builtins


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("mega")


# Hard-coded grid per user request
PERIODS = [
    {"days": 30,  "start_block": 42_746_905, "end_block": 44_042_900, "swaps": 962_848},
    {"days": 60,  "start_block": 41_450_901, "end_block": 44_042_900, "swaps": 2_711_934},
    {"days": 90,  "start_block": 40_154_914, "end_block": 44_042_900, "swaps": 4_159_445},
    {"days": 120, "start_block": 38_858_900, "end_block": 44_042_900, "swaps": 5_340_078},
]

NOISE_GRID = [0.20, 0.30, 0.50]                      # prediction error
EXIT_VOL_GRID = [0.004, 0.006, 0.008, 0.012, 0.02]    # HIGH-end pull-out thresholds
SLIPPAGE_FIXED = 2                                    # realistic extra bps
REENTRY_RATIO = 0.5                                   # reentry = exit × this


async def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("config", nargs="?",
                    default="backtester_pullout/config/pool_d0b5.generated.yaml")
    ap.add_argument("--no-liq-map", action="store_true",
                    help="Skip LiquidityMap reconstruction; use swap.liquidity directly")
    ap.add_argument("--label", default="megasweep")
    args = ap.parse_args()

    register_builtins()
    base_cfg = load_config(args.config)
    pool = base_cfg.pools[0]
    use_liq_map = not args.no_liq_map

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path("results") / f"{ts}_{args.label}_{pool.symbol.replace('/', '_')}"
    out_dir.mkdir(parents=True, exist_ok=False)
    csv_path = out_dir / "megasweep.csv"
    log.info(f"Output dir: {out_dir}")
    log.info(f"Pool: {pool.symbol} ({pool.address}) · liq_map={'on' if use_liq_map else 'off'}")

    # Save the plan
    (out_dir / "plan.json").write_text(json.dumps({
        "periods": PERIODS, "noise_grid": NOISE_GRID,
        "exit_vol_grid": EXIT_VOL_GRID, "slippage_fixed": SLIPPAGE_FIXED,
        "reentry_ratio": REENTRY_RATIO,
        "pool": pool.model_dump(),
    }, indent=2, default=str))

    all_rows: List[Dict[str, Any]] = []
    total_cells = len(PERIODS) * len(NOISE_GRID) * len(EXIT_VOL_GRID)
    cell_idx = 0

    for period in PERIODS:
        days = period["days"]
        lo, hi = period["start_block"], period["end_block"]
        log.info(f"=== Period {days}d: blocks [{lo}, {hi}] (expected {period['swaps']:,} swaps) ===")

        log.info(f"Loading swaps...")
        swaps = await load_swaps(pool.address, lo, hi)
        log.info(f"  loaded {len(swaps):,}")

        base_map = None
        win_events = None
        if use_liq_map:
            log.info(f"Loading seed mint/burn events (pre-{lo})...")
            seed_events = await load_liq_events_upto(pool.address, lo - 1)
            log.info(f"  seeded {len(seed_events):,}")

            log.info(f"Applying seed to base LiquidityMap...")
            base_map = LiquidityMap()
            base_map.apply(seed_events)
            log.info(f"  {base_map.num_ticks_with_liquidity():,} ticks with L")

            log.info(f"Loading window mint/burn events...")
            win_events = await load_liq_events(pool.address, lo, hi)
            log.info(f"  {len(win_events):,}")

        for noise in NOISE_GRID:
            for exit_vol in EXIT_VOL_GRID:
                cell_idx += 1
                reentry_vol = exit_vol * REENTRY_RATIO
                log.info(
                    f"--- cell {cell_idx}/{total_cells}: "
                    f"{days}d noise={noise} exit_vol={exit_vol} reentry={reentry_vol:.5f} ---"
                )

                # Build a per-cell PredictionConfig + PoolConfig with overrides
                cfg = base_cfg.model_copy(deep=True)
                cfg.backtest.start_block = lo
                cfg.backtest.end_block = hi
                cfg.prediction.noise_sigma = float(noise)
                cfg.pools[0].slippage_bps = int(SLIPPAGE_FIXED)
                # Override strategy params (pydantic dict field)
                cfg.strategy.params["exit_vol_threshold"] = float(exit_vol)
                cfg.strategy.params["reentry_vol_threshold"] = float(reentry_vol)

                # Fresh map copy per cell, or None if disabled
                liq_map = (LiquidityMap(net=dict(base_map.net))
                           if base_map is not None else None)

                strategy = build_strategy(cfg.strategy.type, cfg.strategy.params)
                res = run_backtest(
                    swaps, cfg.pools[0], cfg.prediction, strategy,
                    seed=cfg.seed, cell_index=cell_idx,
                    liq_map=liq_map, liq_events_in_window=win_events,
                )
                metrics = compute_all_metrics(res, decimals1=cfg.pools[0].decimals1)

                n_exit = sum(1 for a in res.actions if a.get("action") == "EXIT")
                n_enter = sum(1 for a in res.actions if a.get("action") == "ENTER")
                n_reb = sum(1 for a in res.actions if a.get("action") == "REBALANCE")

                row = {
                    "cell_index": cell_idx,
                    "days": days, "noise_sigma": noise,
                    "exit_vol": exit_vol, "reentry_vol": reentry_vol,
                    "slippage_bps": int(SLIPPAGE_FIXED),
                    "start_block": lo, "end_block": hi, "n_swaps": len(swaps),
                    "n_enter": n_enter, "n_exit": n_exit, "n_rebalance": n_reb,
                    "excess_return_vs_hodl": excess_return_vs_hodl(metrics),
                    "hodl_return": metrics["hodl"]["total_return"],
                    "passive_return": metrics["passive"]["total_return"],
                    "strategy_return": metrics["strategy"]["total_return"],
                    "strategy_sharpe": metrics["strategy"]["sharpe"],
                    "strategy_max_dd": metrics["strategy"]["max_drawdown"],
                    "strategy_tim": metrics["strategy"]["time_in_market"],
                    "strategy_costs": metrics["strategy"]["total_costs"],
                }
                all_rows.append(row)

                # Incremental save — resume-friendly
                pd.DataFrame(all_rows).to_csv(csv_path, index=False)
                log.info(
                    f"    excess_vs_hodl = {row['excess_return_vs_hodl']:+.4%}  "
                    f"(strategy {row['strategy_return']:+.2%}, hodl {row['hodl_return']:+.2%}, "
                    f"actions {n_enter}E/{n_exit}X/{n_reb}R)"
                )

    log.info(f"Megasweep complete: {len(all_rows)} cells → {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

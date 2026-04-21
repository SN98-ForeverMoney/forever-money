"""12-cell price_miner sweep on WETH/USDC 120d.

Axes:
  return_noise_k        ∈ {0.0, 0.5, 1.0, 2.0}
  rebalance_distance_ticks ∈ {10, 30, 100}

k=0.0 is the perfect-foresight ceiling — if that doesn't beat HODL, the
whole thesis is dead.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from backtester_pullout.backtester.config import load_config
from backtester_pullout.backtester.data import load_swaps
from backtester_pullout.backtester.engine import run_backtest
from backtester_pullout.backtester.metrics import (
    compute_all_metrics, excess_return_vs_hodl,
)
from backtester_pullout.backtester.strategies import build_strategy, register_builtins


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("price")


NOISE_K_GRID = [0.0, 0.5, 1.0, 2.0]
REBAL_DISTANCE_GRID = [10, 30, 100]


async def main() -> int:
    register_builtins()
    cfg = load_config("backtester_pullout/config/pool_d0b5.generated.yaml")
    pool = cfg.pools[0]

    # Force the strategy to price_miner; keep width/volatility window sane
    cfg.strategy.type = "price_miner"
    cfg.strategy.params = {
        "width_factor": 3.0,
        "volatility_window": 10,
        "min_width_spacings": 25,
        "min_blocks_between_decisions": 450,
        # rebalance_distance_ticks filled per cell
    }

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path("results") / f"{ts}_price_sweep_120d"
    out_dir.mkdir(parents=True, exist_ok=False)
    log.info(f"Output: {out_dir}")

    log.info(f"Loading swaps for {pool.symbol} "
             f"[{cfg.backtest.start_block}, {cfg.backtest.end_block}]...")
    swaps = await load_swaps(pool.address, cfg.backtest.start_block,
                              cfg.backtest.end_block)
    log.info(f"  loaded {len(swaps):,}")

    rows = []
    cell = 0
    total = len(NOISE_K_GRID) * len(REBAL_DISTANCE_GRID)
    for k in NOISE_K_GRID:
        for rebal_d in REBAL_DISTANCE_GRID:
            cell += 1
            cell_cfg = cfg.model_copy(deep=True)
            cell_cfg.prediction.return_noise_k = float(k)
            cell_cfg.strategy.params["rebalance_distance_ticks"] = int(rebal_d)

            strat = build_strategy(cell_cfg.strategy.type, cell_cfg.strategy.params)
            res = run_backtest(
                swaps, cell_cfg.pools[0], cell_cfg.prediction, strat,
                seed=cell_cfg.seed, cell_index=cell,
                liq_map=None, liq_events_in_window=None,
            )
            m = compute_all_metrics(res, decimals1=cell_cfg.pools[0].decimals1)
            n_reb = sum(1 for a in res.actions if a.get("action") == "REBALANCE")
            n_enter = sum(1 for a in res.actions if a.get("action") == "ENTER")
            row = {
                "cell": cell, "noise_k": k, "rebal_dist_ticks": rebal_d,
                "hodl_return": m["hodl"]["total_return"],
                "strategy_return": m["strategy"]["total_return"],
                "excess_return_vs_hodl": excess_return_vs_hodl(m),
                "n_rebalance": n_reb, "n_enter": n_enter,
                "strategy_max_dd": m["strategy"]["max_drawdown"],
                "strategy_tim": m["strategy"]["time_in_market"],
            }
            rows.append(row)
            pd.DataFrame(rows).to_csv(out_dir / "sweep.csv", index=False)
            log.info(
                f"  cell {cell}/{total} k={k} dist={rebal_d}: "
                f"excess={row['excess_return_vs_hodl']:+.4%} "
                f"(strat {row['strategy_return']:+.4%}, hodl {row['hodl_return']:+.4%}, "
                f"reb={n_reb})"
            )

    log.info(f"\nDone. Results: {out_dir / 'sweep.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

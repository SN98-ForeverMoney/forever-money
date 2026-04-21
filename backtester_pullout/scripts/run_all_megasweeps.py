"""Run megasweep on every config in backtester_pullout/config/generated/.

Skips configs in SKIP_LIST (already done). Saves each pool's sweep under
results/<ts>_all/<pool>/megasweep.csv and writes a combined all_pools.csv
at the end with best-per-pool-period rows.
"""
from __future__ import annotations

import asyncio
import csv
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import List

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
log = logging.getLogger("all")


# Sweep grid (matches high-exit megasweep)
PERIODS = [
    {"days": 30,  "start_block": 42_746_905, "end_block": 44_042_900},
    {"days": 60,  "start_block": 41_450_901, "end_block": 44_042_900},
    {"days": 90,  "start_block": 40_154_914, "end_block": 44_042_900},
    {"days": 120, "start_block": 38_858_900, "end_block": 44_042_900},
]
NOISE_GRID = [0.20, 0.30, 0.50]
EXIT_VOL_GRID = [0.004, 0.006, 0.008, 0.012, 0.02]
SLIPPAGE_FIXED = 2
REENTRY_RATIO = 0.5

SKIP_LIST = {
    "pool_d0b53d92.yaml",  # WETH/USDC done
    "pool_4e962bb3.yaml",  # USDC/cbBTC done
    "pool_70acdf2a.yaml",  # WETH/cbBTC done
}


async def run_one_pool(cfg_path: Path, out_dir: Path) -> List[dict]:
    base_cfg = load_config(cfg_path)
    pool = base_cfg.pools[0]
    sym_safe = pool.symbol.replace("/", "_")
    log.info(f"\n=== Pool {sym_safe} ({pool.address}) ===")

    pool_out = out_dir / sym_safe
    pool_out.mkdir(parents=True, exist_ok=True)
    csv_path = pool_out / "megasweep.csv"

    rows: List[dict] = []
    cell_idx = 0
    total = len(PERIODS) * len(NOISE_GRID) * len(EXIT_VOL_GRID)

    for period in PERIODS:
        days, lo, hi = period["days"], period["start_block"], period["end_block"]
        log.info(f"  Period {days}d: loading swaps...")
        swaps = await load_swaps(pool.address, lo, hi)
        log.info(f"    loaded {len(swaps):,}")
        if len(swaps) < 100:
            log.warning(f"    too few swaps ({len(swaps)}) — skipping period")
            continue

        for noise in NOISE_GRID:
            for exit_vol in EXIT_VOL_GRID:
                cell_idx += 1
                reentry_vol = exit_vol * REENTRY_RATIO
                cfg = base_cfg.model_copy(deep=True)
                cfg.backtest.start_block = lo
                cfg.backtest.end_block = hi
                cfg.prediction.noise_sigma = float(noise)
                cfg.pools[0].slippage_bps = int(SLIPPAGE_FIXED)
                cfg.strategy.params["exit_vol_threshold"] = float(exit_vol)
                cfg.strategy.params["reentry_vol_threshold"] = float(reentry_vol)

                strategy = build_strategy(cfg.strategy.type, cfg.strategy.params)
                try:
                    res = run_backtest(
                        swaps, cfg.pools[0], cfg.prediction, strategy,
                        seed=cfg.seed, cell_index=cell_idx,
                        liq_map=None, liq_events_in_window=None,
                    )
                    metrics = compute_all_metrics(res, decimals1=cfg.pools[0].decimals1)
                    n_exit = sum(1 for a in res.actions if a.get("action") == "EXIT")
                    n_enter = sum(1 for a in res.actions if a.get("action") == "ENTER")
                    n_reb = sum(1 for a in res.actions if a.get("action") == "REBALANCE")
                    row = {
                        "pool": pool.symbol, "address": pool.address,
                        "fee_tier": pool.fee_tier,
                        "cell_index": cell_idx, "days": days,
                        "noise_sigma": noise, "exit_vol": exit_vol,
                        "reentry_vol": reentry_vol, "slippage_bps": SLIPPAGE_FIXED,
                        "n_swaps": len(swaps),
                        "n_enter": n_enter, "n_exit": n_exit, "n_rebalance": n_reb,
                        "excess_return_vs_hodl": excess_return_vs_hodl(metrics),
                        "hodl_return": metrics["hodl"]["total_return"],
                        "strategy_return": metrics["strategy"]["total_return"],
                        "strategy_sharpe": metrics["strategy"]["sharpe"],
                        "strategy_max_dd": metrics["strategy"]["max_drawdown"],
                        "strategy_tim": metrics["strategy"]["time_in_market"],
                    }
                    rows.append(row)
                    pd.DataFrame(rows).to_csv(csv_path, index=False)
                    log.info(
                        f"    cell {cell_idx}/{total} [{days}d n={noise} x={exit_vol}] "
                        f"excess={row['excess_return_vs_hodl']:+.4%} "
                        f"({n_enter}E/{n_exit}X/{n_reb}R)"
                    )
                except Exception as e:
                    log.error(f"    cell {cell_idx} FAIL: {e}")
                    rows.append({
                        "pool": pool.symbol, "address": pool.address,
                        "cell_index": cell_idx, "days": days,
                        "noise_sigma": noise, "exit_vol": exit_vol,
                        "error": str(e)[:200],
                    })
                    pd.DataFrame(rows).to_csv(csv_path, index=False)

    return rows


async def main() -> int:
    register_builtins()
    cfg_dir = Path("backtester_pullout/config/generated")
    configs = sorted(p for p in cfg_dir.glob("pool_*.yaml") if p.name not in SKIP_LIST)
    log.info(f"Running {len(configs)} pool sweeps (skipping {len(SKIP_LIST)} done)")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path("results") / f"{ts}_all_pools"
    out.mkdir(parents=True, exist_ok=False)
    log.info(f"Output: {out}")

    all_rows: List[dict] = []
    for i, cfg_path in enumerate(configs, 1):
        log.info(f"\n[{i}/{len(configs)}] {cfg_path.name}")
        try:
            rows = await run_one_pool(cfg_path, out)
            all_rows.extend(rows)
            pd.DataFrame(all_rows).to_csv(out / "all_pools.csv", index=False)
        except Exception as e:
            log.error(f"Pool {cfg_path.name} failed entirely: {e}")

    log.info(f"\nAll done. Combined: {out / 'all_pools.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

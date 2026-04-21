"""Cartesian-product sweep runner.

Given a base config and a `sweep.grid` mapping dotted-path → list of values,
expand every combination into an independent cell, run each through the
engine, and aggregate metrics into a DataFrame.

Determinism: each cell gets its own seed via `derive_seed(base_seed, cell_index)`.
The manifest records the seed for every cell so results are reproducible.

Cells are independent per the user's requirement — no precomputed sharing.
The swap frame is loaded ONCE per sweep and reused (read-only pandas),
because reloading from DB would dominate runtime.
"""
from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

import pandas as pd

from backtester_pullout.backtester.config import Config
from backtester_pullout.backtester.engine import EngineResult, run_backtest
from backtester_pullout.backtester.metrics import (
    compute_all_metrics,
    excess_return_vs_hodl,
)
from backtester_pullout.backtester.strategies import build_strategy
from backtester_pullout.backtester.vol import derive_seed

logger = logging.getLogger(__name__)


def _set_by_path(obj: Any, dotted: str, value: Any) -> None:
    """Set a field using a dotted path. Supports dict params and attribute access
    on pydantic models."""
    parts = dotted.split(".")
    cur = obj
    for p in parts[:-1]:
        if isinstance(cur, dict):
            cur = cur[p]
        else:
            cur = getattr(cur, p)
    last = parts[-1]
    if isinstance(cur, dict):
        cur[last] = value
    else:
        setattr(cur, last, value)


def expand_grid(grid: Dict[str, List[Any]]) -> List[Dict[str, Any]]:
    """Cartesian product of the grid. Each output dict maps dotted-path → value."""
    if not grid:
        return [{}]
    keys = list(grid.keys())
    values = [grid[k] for k in keys]
    return [dict(zip(keys, combo)) for combo in itertools.product(*values)]


@dataclass
class SweepCellResult:
    cell_index: int
    seed: int
    overrides: Dict[str, Any]
    metrics: Dict[str, Dict[str, float]]
    excess_return_vs_hodl: float


def run_sweep(
    base_cfg: Config,
    swaps: pd.DataFrame,
    *,
    log_progress: bool = True,
) -> Tuple[pd.DataFrame, List[SweepCellResult]]:
    """Run every cell in base_cfg.sweep.grid and return (aggregated_df, cell_results).

    `swaps` is the pre-loaded swap frame for base_cfg.pools[0] over the full
    backtest range.
    """
    if base_cfg.sweep is None or not base_cfg.sweep.grid:
        raise ValueError("no sweep grid on config")

    cells = expand_grid(base_cfg.sweep.grid)
    results: List[SweepCellResult] = []

    for idx, overrides in enumerate(cells):
        # Deep copy the base config for per-cell mutation
        cfg = base_cfg.model_copy(deep=True)
        for path, value in overrides.items():
            # Remove the leading namespace for strategy.params
            _set_by_path(cfg, path, value)

        # Re-validate (pydantic): the cell-specific prediction config must still
        # satisfy horizon/bucket >= 5. Re-constructing via model_validate raises
        # if not.
        cfg = Config.model_validate(cfg.model_dump())

        pool = cfg.pools[0]
        seed = derive_seed(cfg.seed, idx)
        strategy = build_strategy(cfg.strategy.type, cfg.strategy.params)

        if log_progress:
            logger.info(f"[sweep] cell {idx + 1}/{len(cells)} overrides={overrides}")

        res = run_backtest(
            swaps, pool, cfg.prediction, strategy,
            seed=cfg.seed, cell_index=idx,
        )
        metrics = compute_all_metrics(res, decimals1=pool.decimals1)

        results.append(SweepCellResult(
            cell_index=idx,
            seed=seed,
            overrides=overrides,
            metrics=metrics,
            excess_return_vs_hodl=excess_return_vs_hodl(metrics),
        ))

    # Aggregate as a flat DataFrame: one row per cell, columns for overrides
    # plus headline metrics.
    rows = []
    for r in results:
        row = {"cell_index": r.cell_index, "seed": r.seed,
               "excess_return_vs_hodl": r.excess_return_vs_hodl}
        for k, v in r.overrides.items():
            row[k] = v
        for leg in ("hodl", "passive", "strategy"):
            for mkey, mval in r.metrics[leg].items():
                row[f"{leg}.{mkey}"] = mval
        rows.append(row)
    df = pd.DataFrame(rows)
    return df, results

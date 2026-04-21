"""Performance metrics on equity curves.

Inputs are equity time-series (token1 raw int). Returns a dict of scalars per
leg: total_return, sharpe, max_drawdown, time_in_market, num_rebalances,
total_costs.

Sharpe here is a pseudo-Sharpe on per-swap log returns (mean/std * sqrt(N)).
It's not annualized — we keep it raw for cross-comparison within a run.
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd

from backtester_pullout.backtester.engine import EngineResult


def _safe_log_returns(equity: np.ndarray) -> np.ndarray:
    eq = equity.astype(np.float64)
    eq = np.where(eq <= 0, np.nan, eq)
    with np.errstate(invalid="ignore", divide="ignore"):
        r = np.diff(np.log(eq))
    return r[np.isfinite(r)]


def _max_drawdown(equity: np.ndarray) -> float:
    eq = equity.astype(np.float64)
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / np.where(peak == 0, 1, peak)
    return float(dd.min()) if len(dd) else 0.0


def compute_leg_metrics(
    equity: np.ndarray,
    *,
    in_range_swaps: int = 0,
    observed_swaps: int = 0,
    num_rebalances: int = 0,
    total_costs_token1: int = 0,
    cost_scale: float = 1.0,
) -> Dict[str, float]:
    if len(equity) < 2:
        return {
            "initial": 0.0, "final": 0.0, "total_return": 0.0,
            "sharpe": 0.0, "max_drawdown": 0.0, "time_in_market": 0.0,
            "num_rebalances": float(num_rebalances), "total_costs": 0.0,
        }
    initial = float(equity[0])
    final = float(equity[-1])
    total_return = (final - initial) / initial if initial > 0 else 0.0
    rets = _safe_log_returns(equity)
    if len(rets) > 1 and rets.std(ddof=0) > 0:
        sharpe = float(rets.mean() / rets.std(ddof=0) * np.sqrt(len(rets)))
    else:
        sharpe = 0.0
    mdd = _max_drawdown(equity)
    tim = (in_range_swaps / observed_swaps) if observed_swaps > 0 else 0.0
    return {
        "initial": initial / cost_scale,
        "final": final / cost_scale,
        "total_return": total_return,
        "sharpe": sharpe,
        "max_drawdown": mdd,
        "time_in_market": tim,
        "num_rebalances": float(num_rebalances),
        "total_costs": total_costs_token1 / cost_scale,
    }


def compute_all_metrics(res: EngineResult, *, decimals1: int = 0) -> Dict[str, Dict[str, float]]:
    """All three legs → {leg_name: metric_dict}."""
    scale = 10 ** decimals1
    eq = res.equity
    return {
        "hodl": compute_leg_metrics(
            eq["hodl"].to_numpy(),
            in_range_swaps=0, observed_swaps=0,  # N/A for HODL
            num_rebalances=0, total_costs_token1=0,
            cost_scale=scale,
        ),
        "passive": compute_leg_metrics(
            eq["passive"].to_numpy(),
            in_range_swaps=res.final_passive.in_range_swap_count,
            observed_swaps=res.final_passive.observed_swap_count,
            num_rebalances=res.final_passive.num_rebalances,
            total_costs_token1=res.final_passive.costs_paid_token1,
            cost_scale=scale,
        ),
        "strategy": compute_leg_metrics(
            eq["strategy"].to_numpy(),
            in_range_swaps=res.final_strategy.in_range_swap_count,
            observed_swaps=res.final_strategy.observed_swap_count,
            num_rebalances=res.final_strategy.num_rebalances,
            total_costs_token1=res.final_strategy.costs_paid_token1,
            cost_scale=scale,
        ),
    }


def excess_return_vs_hodl(metrics: Dict[str, Dict[str, float]]) -> float:
    """Primary headline: strategy total_return − HODL total_return."""
    return metrics["strategy"]["total_return"] - metrics["hodl"]["total_return"]

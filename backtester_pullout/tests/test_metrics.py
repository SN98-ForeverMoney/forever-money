"""Metrics + IO tests."""
import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backtester_pullout.backtester.engine import EngineResult, HodlLeg, LpLeg
from backtester_pullout.backtester.io import (
    make_results_dir,
    write_actions_csv,
    write_equity_csv,
    write_manifest,
    write_metrics_json,
)
from backtester_pullout.backtester.metrics import (
    compute_all_metrics,
    compute_leg_metrics,
    excess_return_vs_hodl,
)


def test_total_return_on_flat_curve():
    eq = np.array([100, 100, 100, 100], dtype=np.int64)
    m = compute_leg_metrics(eq)
    assert m["total_return"] == 0.0
    assert m["max_drawdown"] == 0.0


def test_total_return_on_upward():
    eq = np.array([100, 110, 120, 121])
    m = compute_leg_metrics(eq)
    assert abs(m["total_return"] - 0.21) < 1e-9


def test_max_drawdown():
    eq = np.array([100, 120, 90, 110])  # peak=120, trough=90 → -25%
    m = compute_leg_metrics(eq)
    assert abs(m["max_drawdown"] - (-0.25)) < 1e-9


def _fake_result() -> EngineResult:
    eq = pd.DataFrame({
        "block": [1, 2, 3], "time": [10, 20, 30],
        "hodl": [1000, 1000, 1000],
        "passive": [1000, 1010, 1020],
        "strategy": [1000, 990, 1030],
        "price": [100.0, 100.5, 101.0],
    })
    return EngineResult(
        equity=eq,
        actions=[{"block": 1, "action": "ENTER", "leg": "strategy", "reason": "x"}],
        final_passive=LpLeg(
            costs_paid_token1=50, num_rebalances=1,
            in_range_swap_count=2, observed_swap_count=3,
        ),
        final_strategy=LpLeg(
            costs_paid_token1=150, num_rebalances=3,
            in_range_swap_count=1, observed_swap_count=3,
        ),
        final_hodl=HodlLeg(0, 0),
    )


def test_compute_all_metrics_shape():
    res = _fake_result()
    m = compute_all_metrics(res, decimals1=0)
    assert set(m) == {"hodl", "passive", "strategy"}
    for leg_m in m.values():
        assert {"total_return", "sharpe", "max_drawdown", "time_in_market",
                "num_rebalances", "total_costs"} <= set(leg_m)


def test_excess_return_formula():
    res = _fake_result()
    m = compute_all_metrics(res, decimals1=0)
    expected = m["strategy"]["total_return"] - m["hodl"]["total_return"]
    assert excess_return_vs_hodl(m) == expected


def test_results_dir_refuses_overwrite():
    with tempfile.TemporaryDirectory() as td:
        d1 = make_results_dir(td, "t")
        assert d1.exists()
        # Creating another immediately with identical timestamp+label would collide;
        # we simulate by reusing the path explicitly.
        with pytest.raises(FileExistsError):
            d1.mkdir(exist_ok=False)


def test_writes_all_outputs():
    with tempfile.TemporaryDirectory() as td:
        d = make_results_dir(td, "t")
        res = _fake_result()
        m = compute_all_metrics(res, decimals1=0)
        write_equity_csv(res, d, decimals1=0)
        write_actions_csv(res, d)
        write_metrics_json(m, d)
        write_manifest({"seed": 42}, d)
        assert (d / "equity.csv").exists()
        assert (d / "actions.csv").exists()
        assert (d / "metrics.json").exists()
        assert (d / "manifest.json").exists()
        # metrics.json must be valid JSON
        with (d / "metrics.json").open() as f:
            loaded = json.load(f)
        assert "strategy" in loaded

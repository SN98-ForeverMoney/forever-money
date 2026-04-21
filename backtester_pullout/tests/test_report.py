"""Report generator tests."""
import json
import tempfile
from pathlib import Path

import pandas as pd

from backtester_pullout.backtester.report import write_report


def _make_single(d: Path):
    pd.DataFrame({
        "block": [1, 2, 3], "time": [10, 20, 30],
        "hodl": [100.0, 101.0, 102.0],
        "passive": [100.0, 101.5, 103.0],
        "strategy": [100.0, 100.5, 104.0],
    }).to_csv(d / "equity.csv", index=False)
    pd.DataFrame([
        {"block": 1, "action": "ENTER", "leg": "strategy", "reason": "initial",
         "cost_token1": 10000},
    ]).to_csv(d / "actions.csv", index=False)
    (d / "metrics.json").write_text(json.dumps({
        "hodl": {"initial": 100, "final": 102, "total_return": 0.02,
                 "sharpe": 1.5, "max_drawdown": -0.01, "time_in_market": 0.0,
                 "num_rebalances": 0, "total_costs": 0.0},
        "passive": {"initial": 100, "final": 103, "total_return": 0.03,
                    "sharpe": 2.0, "max_drawdown": -0.005, "time_in_market": 0.5,
                    "num_rebalances": 0, "total_costs": 0.01},
        "strategy": {"initial": 100, "final": 104, "total_return": 0.04,
                     "sharpe": 1.8, "max_drawdown": -0.02, "time_in_market": 0.4,
                     "num_rebalances": 3, "total_costs": 0.03},
    }))
    (d / "manifest.json").write_text(json.dumps({
        "pool": {"symbol": "ETH/USDC", "address": "0xabc"},
        "strategy": {"type": "hysteresis", "params": {"threshold_high": 0.02}},
        "seed": 42, "excess_return_vs_hodl": 0.02,
    }))
    # equity.png is optional — skip


def _make_sweep(d: Path):
    pd.DataFrame([
        {"cell_index": 0, "seed": 100, "excess_return_vs_hodl": 0.01,
         "noise": 0.0, "horizon": 300,
         "strategy.total_return": 0.05, "strategy.sharpe": 2.0,
         "strategy.max_drawdown": -0.01, "strategy.num_rebalances": 5,
         "strategy.total_costs": 0.05},
        {"cell_index": 1, "seed": 101, "excess_return_vs_hodl": -0.005,
         "noise": 0.2, "horizon": 300,
         "strategy.total_return": 0.04, "strategy.sharpe": 1.5,
         "strategy.max_drawdown": -0.02, "strategy.num_rebalances": 8,
         "strategy.total_costs": 0.08},
    ]).to_csv(d / "sweep.csv", index=False)
    (d / "cells.json").write_text(json.dumps([
        {"cell_index": 0, "seed": 100, "overrides": {"noise": 0.0},
         "excess_return_vs_hodl": 0.01},
        {"cell_index": 1, "seed": 101, "overrides": {"noise": 0.2},
         "excess_return_vs_hodl": -0.005},
    ]))
    (d / "manifest.json").write_text(json.dumps({
        "pool": {"symbol": "ETH/USDC", "address": "0xabc"},
        "base_seed": 42, "num_cells": 2,
        "grid_keys": ["noise", "horizon"],
    }))


def test_write_single_report():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _make_single(d)
        report = write_report(d)
        assert report.exists()
        html = report.read_text()
        assert "ETH/USDC" in html
        assert "Excess return vs HODL" in html
        assert "Equity curves" in html


def test_write_sweep_report():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _make_sweep(d)
        report = write_report(d)
        assert report.exists()
        html = report.read_text()
        assert "Sweep report" in html
        assert "Best cell" in html
        assert "Heatmaps" in html
        assert "Cells beating HODL" in html


def test_unknown_dir_raises():
    with tempfile.TemporaryDirectory() as td:
        with __import__("pytest").raises(ValueError, match="doesn't look like"):
            write_report(td)

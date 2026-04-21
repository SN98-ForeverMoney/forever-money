"""Plot smoke tests — verify files are produced, no rendering checks."""
import tempfile
from pathlib import Path

import pandas as pd

from backtester_pullout.backtester.config import PoolConfig, TickWidthRange
from backtester_pullout.backtester.engine import EngineResult, HodlLeg, LpLeg
from backtester_pullout.backtester.plots import (
    breakeven_curve,
    equity_curve,
    heatmap,
)


def _pool():
    return PoolConfig(
        address="0x" + "a" * 40, symbol="T/U",
        token0="0x" + "1" * 40, token1="0x" + "2" * 40,
        decimals0=18, decimals1=6, fee_tier=3000, tick_spacing=10,
        range=TickWidthRange(type="tick_width", width_ticks=200),
        position_size_usd=10000, tx_cost_usd=0.01, slippage_bps=200,
    )


def test_equity_curve_produces_png():
    res = EngineResult(
        equity=pd.DataFrame({
            "block": [1, 2, 3, 4, 5],
            "time": [10, 20, 30, 40, 50],
            "hodl":     [1_000_000_000, 1_001_000_000, 1_002_000_000, 1_003_000_000, 1_004_000_000],
            "passive":  [1_000_000_000, 1_001_500_000, 1_002_500_000, 1_003_500_000, 1_005_000_000],
            "strategy": [1_000_000_000, 1_000_500_000, 1_001_500_000, 1_003_000_000, 1_006_000_000],
            "price":    [3000.0, 3001.5, 3002.0, 3010.0, 3005.0],
        }),
        actions=[{"block": 3, "action": "EXIT", "leg": "strategy", "reason": "s"}],
        final_passive=LpLeg(), final_strategy=LpLeg(), final_hodl=HodlLeg(0, 0),
    )
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "eq.png"
        equity_curve(res, _pool(), p)
        assert p.exists() and p.stat().st_size > 0


def test_heatmap_produces_png():
    df = pd.DataFrame({
        "x": [0.0, 0.0, 0.1, 0.1, 0.2, 0.2],
        "y": [60, 300, 60, 300, 60, 300],
        "v": [0.01, -0.02, 0.03, -0.01, 0.0, 0.0],
    })
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "h.png"
        heatmap(df, "x", "y", "v", p)
        assert p.exists() and p.stat().st_size > 0


def test_breakeven_curve_produces_png():
    df = pd.DataFrame({
        "noise": [0.0, 0.1, 0.2, 0.3, 0.0, 0.1, 0.2, 0.3],
        "excess": [0.02, 0.01, 0.0, -0.01, 0.03, 0.02, 0.01, 0.005],
        "horizon": [60, 60, 60, 60, 300, 300, 300, 300],
    })
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "bk.png"
        breakeven_curve(df, "noise", "excess", "horizon", p)
        assert p.exists() and p.stat().st_size > 0

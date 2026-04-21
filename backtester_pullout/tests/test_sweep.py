"""Sweep runner tests."""
import pandas as pd
import pytest

from backtester_pullout.backtester.config import Config
from backtester_pullout.backtester.strategies import register_builtins
from backtester_pullout.backtester.sweep import expand_grid, run_sweep
from validator.utils.math import UniswapV3Math

register_builtins()


def _cfg_dict():
    return {
        "pools": [{
            "address": "0x" + "a" * 40, "symbol": "T/U",
            "token0": "0x" + "1" * 40, "token1": "0x" + "2" * 40,
            "decimals0": 18, "decimals1": 6, "fee_tier": 3000, "tick_spacing": 10,
            "range": {"type": "tick_width", "width_ticks": 200},
            "position_size_usd": 10000, "tx_cost_usd": 0.0, "slippage_bps": 0,
        }],
        "backtest": {"start_block": 1_000_000, "end_block": 1_000_999},
        "prediction": {"horizon_blocks": 25, "noise_sigma": 0.0, "vol_bucket_blocks": 5},
        "strategy": {"type": "binary", "params": {"threshold": 0.01}},
        "sweep": {"grid": {
            "prediction.noise_sigma": [0.0, 0.1],
            "strategy.params.threshold": [0.005, 0.02],
        }},
        "seed": 7,
    }


def _synth_swaps(n: int):
    sqrt_p = UniswapV3Math.get_sqrt_ratio_at_tick(0)
    return pd.DataFrame([{
        "block": 1_000_000 + i, "time": 1_700_000_000 + i,
        "tick": 0, "sqrt_price_x96": sqrt_p,
        "amount0": 10**17, "amount1": -10**17, "liquidity": 10**22,
    } for i in range(n)])


def test_expand_grid_cartesian():
    g = {"a": [1, 2], "b": ["x", "y", "z"]}
    cells = expand_grid(g)
    assert len(cells) == 6


def test_expand_grid_empty():
    assert expand_grid({}) == [{}]


def test_run_sweep_runs_every_cell():
    cfg = Config.model_validate(_cfg_dict())
    swaps = _synth_swaps(300)
    df, cells = run_sweep(cfg, swaps, log_progress=False)
    assert len(cells) == 4
    assert len(df) == 4
    assert "prediction.noise_sigma" in df.columns
    assert "strategy.params.threshold" in df.columns
    assert "excess_return_vs_hodl" in df.columns
    # Seeds should all differ
    seeds = [c.seed for c in cells]
    assert len(set(seeds)) == 4


def test_run_sweep_determinism():
    cfg = Config.model_validate(_cfg_dict())
    swaps = _synth_swaps(300)
    df_a, _ = run_sweep(cfg, swaps, log_progress=False)
    df_b, _ = run_sweep(cfg, swaps, log_progress=False)
    pd.testing.assert_frame_equal(df_a, df_b)


def test_run_sweep_validates_cells():
    """A cell that would violate horizon/bucket >= 5 must raise on re-validate."""
    d = _cfg_dict()
    # horizon=25, bucket=5 → ratio=5. Now sweep horizon down to 10 → ratio=2.
    d["sweep"] = {"grid": {"prediction.horizon_blocks": [10, 25]}}
    cfg = Config.model_validate(d)
    swaps = _synth_swaps(300)
    with pytest.raises(Exception):  # pydantic ValidationError
        run_sweep(cfg, swaps, log_progress=False)

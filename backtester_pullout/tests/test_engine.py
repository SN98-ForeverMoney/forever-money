"""Engine unit tests — no DB required.

Uses synthetic swap frames built in-memory.
"""
import pandas as pd
import pytest

from backtester_pullout.backtester.config import (
    AbsoluteRange,
    PoolConfig,
    PredictionConfig,
    TickWidthRange,
)
from backtester_pullout.backtester.engine import (
    HodlLeg,
    LpLeg,
    initial_amounts_5050,
    resolve_range,
    run_backtest,
)
from backtester_pullout.backtester.strategies import register_builtins, build_strategy
from validator.utils.math import UniswapV3Math

register_builtins()


def _pool_cfg() -> PoolConfig:
    return PoolConfig(
        address="0x" + "a" * 40,
        symbol="T/U",
        token0="0x" + "1" * 40,
        token1="0x" + "2" * 40,
        decimals0=18,
        decimals1=6,
        fee_tier=3000,
        tick_spacing=10,
        range=TickWidthRange(type="tick_width", width_ticks=200),
        position_size_usd=10_000,
        tx_cost_usd=0.01,
        slippage_bps=200,
    )


def _pred_cfg() -> PredictionConfig:
    # Small bucket/horizon so short synthetic swap frames satisfy VolOracle's
    # minimum-span requirement. horizon/bucket = 5 sits at the validation floor.
    return PredictionConfig(horizon_blocks=25, noise_sigma=0.0, vol_bucket_blocks=5)


def _make_swaps(n: int, base_tick: int = 0, amount_in: int = 10**18) -> pd.DataFrame:
    """Synthetic swaps all at the same price. amount0 positive → token0-in side."""
    sqrt_p = UniswapV3Math.get_sqrt_ratio_at_tick(base_tick)
    rows = []
    for i in range(n):
        rows.append({
            "block": 1_000_000 + i,
            "time": 1_700_000_000 + i * 2,
            "tick": base_tick,
            "sqrt_price_x96": sqrt_p,
            "amount0": amount_in,
            "amount1": -amount_in // 2,
            "liquidity": 10**22,  # pool L big enough that our dilution is small
        })
    return pd.DataFrame(rows)


def test_initial_5050_at_tick_zero():
    sqrt_p = UniswapV3Math.get_sqrt_ratio_at_tick(0)
    a0, a1 = initial_amounts_5050(position_size_token1_raw=1_000_000_000, sqrt_price_x96=sqrt_p)
    # At tick 0, price=1 (raw), so half value in each side. Expect ~500M each.
    assert abs(a0 - 500_000_000) < 1000
    assert abs(a1 - 500_000_000) < 1000


def test_resolve_range_snaps_to_spacing():
    pool = _pool_cfg()  # width=200, spacing=10
    lower, upper = resolve_range(pool, entry_tick=5)
    assert lower % 10 == 0
    assert upper % 10 == 0
    assert upper - lower == 200


def test_always_in_strategy_matches_passive():
    """With always_in strategy, strategy leg should track passive exactly."""
    swaps = _make_swaps(100)
    strat = build_strategy("always_in")
    res = run_backtest(swaps, _pool_cfg(), _pred_cfg(), strat)
    assert (res.equity["strategy"] == res.equity["passive"]).all()


def test_hodl_is_flat_at_constant_price():
    """Constant price → HODL value is constant too."""
    swaps = _make_swaps(50)
    strat = build_strategy("always_in")
    res = run_backtest(swaps, _pool_cfg(), _pred_cfg(), strat)
    assert res.equity["hodl"].nunique() == 1  # all equal


def test_passive_lp_accrues_fees():
    """LP in-range earns fees → final passive value > initial."""
    swaps = _make_swaps(1000, base_tick=0, amount_in=10**18)
    strat = build_strategy("always_in")
    res = run_backtest(swaps, _pool_cfg(), _pred_cfg(), strat)
    first = res.equity["passive"].iloc[0]
    last = res.equity["passive"].iloc[-1]
    assert last > first  # fees accrued
    # And passive must beat HODL at constant price (free fees)
    assert res.equity["passive"].iloc[-1] > res.equity["hodl"].iloc[-1]


def test_passive_is_stationary_out_of_range():
    """If all swaps are far out of range, passive LP accrues no fees → flat equity."""
    # Force an absolute range far from the swap price so first-swap entry can't
    # re-center on it.
    pool = _pool_cfg().model_copy(update={
        "range": AbsoluteRange(type="absolute", tick_lower=-100, tick_upper=100),
    })
    swaps = _make_swaps(50, base_tick=10_000)
    strat = build_strategy("always_in")
    res = run_backtest(swaps, pool, _pred_cfg(), strat)
    assert res.final_passive.position is None or res.final_passive.position.fees0 == 0
    assert res.equity["passive"].nunique() == 1


def test_empty_swaps_raises():
    with pytest.raises(ValueError):
        run_backtest(pd.DataFrame(columns=["block", "time", "tick", "sqrt_price_x96", "amount0", "amount1", "liquidity"]),
                     _pool_cfg(), _pred_cfg(), build_strategy("always_in"))

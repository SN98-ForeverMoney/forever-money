"""Cost accounting tests."""
import pandas as pd

from backtester_pullout.backtester.config import (
    AbsoluteRange,
    PoolConfig,
    PredictionConfig,
    TickWidthRange,
)
from backtester_pullout.backtester.engine import run_backtest
from backtester_pullout.backtester.strategies import build_strategy, register_builtins
from validator.utils.math import UniswapV3Math

register_builtins()


def _pool(tx_cost_usd: float = 0.01) -> PoolConfig:
    return PoolConfig(
        address="0x" + "a" * 40,
        symbol="T/U",
        token0="0x" + "1" * 40,
        token1="0x" + "2" * 40,
        decimals0=18, decimals1=6,
        fee_tier=3000, tick_spacing=10,
        range=TickWidthRange(type="tick_width", width_ticks=200),
        position_size_usd=10_000,
        tx_cost_usd=tx_cost_usd, slippage_bps=0,
        action_delay_blocks=0,  # tests want immediate execution
    )


def _pred() -> PredictionConfig:
    return PredictionConfig(horizon_blocks=25, noise_sigma=0.0, vol_bucket_blocks=5)


def _swaps(n: int, base_tick: int = 0) -> pd.DataFrame:
    sqrt_p = UniswapV3Math.get_sqrt_ratio_at_tick(base_tick)
    return pd.DataFrame([{
        "block": 1_000_000 + i, "time": 1_700_000_000 + i,
        "tick": base_tick, "sqrt_price_x96": sqrt_p,
        "amount0": 10**18, "amount1": -10**18, "liquidity": 10**22,
    } for i in range(n)])


def test_initial_entry_cost_charged_to_both_legs():
    swaps = _swaps(100)
    strat = build_strategy("always_in")
    res = run_backtest(swaps, _pool(tx_cost_usd=1.0), _pred(), strat)
    # tx_cost_usd=1.0 with decimals1=6 → 1,000,000 raw units
    assert res.final_passive.costs_paid_token1 == 1_000_000
    assert res.final_strategy.costs_paid_token1 == 1_000_000


def test_zero_cost_means_zero_accounting():
    swaps = _swaps(50)
    res = run_backtest(swaps, _pool(tx_cost_usd=0.0), _pred(), build_strategy("always_in"))
    assert res.final_passive.costs_paid_token1 == 0
    assert res.final_strategy.costs_paid_token1 == 0


def test_exit_and_reentry_each_charge_cost():
    # Force exits/entries via binary threshold tied to synthetic vol.
    # With constant prices, vol = 0, so predicted_vol = 0 (plus noise ε~N(0,0)=0).
    # Binary threshold at 0.01: pv=0 <= 0.01 → ENTER when out, HOLD when in.
    # So we never actually exit. Need to trigger exits differently.
    # Easier: craft a custom stub strategy that exits on first mid-block, re-enters later.
    from backtester_pullout.backtester.strategies.base import (
        DecisionContext, Strategy, StrategyAction, register_strategy,
    )

    @register_strategy("_test_toggle")
    class Toggle(Strategy):
        def __init__(self):
            self.calls = 0
        def decide(self, ctx: DecisionContext) -> StrategyAction:
            self.calls += 1
            if self.calls == 50 and ctx.in_position:
                return StrategyAction.exit()
            if self.calls == 80 and not ctx.in_position:
                tl, tu = ctx.default_range_fn(ctx.current_tick)
                return StrategyAction.enter(tl, tu)
            return StrategyAction.hold()

    swaps = _swaps(100)
    strat = build_strategy("_test_toggle")
    res = run_backtest(swaps, _pool(tx_cost_usd=0.5), _pred(), strat)
    # initial entry + 1 exit + 1 reentry = 3 × 500_000 = 1_500_000
    assert res.final_strategy.costs_paid_token1 == 1_500_000
    assert res.final_strategy.num_rebalances == 2  # exit + reentry (initial doesn't count)
    # passive only paid initial entry
    assert res.final_passive.costs_paid_token1 == 500_000


def test_action_log_records_costs():
    swaps = _swaps(50)
    res = run_backtest(swaps, _pool(tx_cost_usd=1.0), _pred(), build_strategy("always_in"))
    assert len(res.actions) >= 1
    assert res.actions[0]["action"] == "ENTER"
    assert res.actions[0]["cost_token1"] == 1_000_000

"""Tests for swap-to-ratio + action delay."""
import pandas as pd

from backtester_pullout.backtester.config import (
    AbsoluteRange,
    PoolConfig,
    PredictionConfig,
    TickWidthRange,
)
from backtester_pullout.backtester.engine import run_backtest
from backtester_pullout.backtester.position import swap_to_ratio
from backtester_pullout.backtester.strategies import build_strategy, register_builtins
from backtester_pullout.backtester.strategies.base import (
    ActionKind, DecisionContext, Strategy, StrategyAction, register_strategy,
)
from validator.utils.math import UniswapV3Math

register_builtins()


# --- swap_to_ratio --------------------------------------------------------

def test_swap_to_ratio_price_above_range_goes_all_token1():
    sqrt_p = UniswapV3Math.get_sqrt_ratio_at_tick(1000)  # price far above range [-100,100]
    a0, a1, sw, side = swap_to_ratio(
        amount0=10**18, amount1=10**6,
        sqrt_price_x96=sqrt_p, tick_lower=-100, tick_upper=100,
        extra_slippage_bps=0,
    )
    # Range is below current price → position is 100% token1 → need all token1
    assert a0 == 0
    assert a1 > 10**6  # converted some token0 to token1
    assert side == 0   # sold token0


def test_swap_to_ratio_price_below_range_goes_all_token0():
    sqrt_p = UniswapV3Math.get_sqrt_ratio_at_tick(-1000)
    a0, a1, sw, side = swap_to_ratio(
        amount0=10**18, amount1=10**18,
        sqrt_price_x96=sqrt_p, tick_lower=-100, tick_upper=100,
        extra_slippage_bps=0,
    )
    assert a1 == 0
    assert a0 > 10**18
    assert side == 1


def test_swap_to_ratio_in_range_balances():
    """Price centered in range → target ratio ~50/50. Starting lopsided → swap."""
    sqrt_p = UniswapV3Math.get_sqrt_ratio_at_tick(0)
    # Start with all token1
    a0, a1, sw, side = swap_to_ratio(
        amount0=0, amount1=10**18,
        sqrt_price_x96=sqrt_p, tick_lower=-100, tick_upper=100,
        extra_slippage_bps=0,
    )
    assert a0 > 0
    assert a1 > 0
    assert side == 1  # sold token1


def test_swap_to_ratio_slippage_reduces_received():
    sqrt_p = UniswapV3Math.get_sqrt_ratio_at_tick(0)
    _, a1_no_slip, _, _ = swap_to_ratio(10**18, 0, sqrt_p, -100, 100,
                                        extra_slippage_bps=0)
    _, a1_200bps, _, _ = swap_to_ratio(10**18, 0, sqrt_p, -100, 100,
                                       extra_slippage_bps=200)
    # With slippage, we received less token1 after selling the surplus token0
    assert a1_200bps < a1_no_slip


def test_swap_to_ratio_v3_dynamic_slippage():
    """V3 dynamic: larger swap in thinner pool → more slippage."""
    from backtester_pullout.backtester.position import simulate_swap_v3
    sqrt_p = UniswapV3Math.get_sqrt_ratio_at_tick(0)
    # Thin pool
    out_thin, _ = simulate_swap_v3(10**18, 0, sqrt_p, active_L=10**20,
                                   pool_fee_tier=500)
    # Deep pool (same swap size)
    out_deep, _ = simulate_swap_v3(10**18, 0, sqrt_p, active_L=10**24,
                                   pool_fee_tier=500)
    # Deeper pool → less slippage → higher output
    assert out_deep > out_thin


# --- delay end-to-end -----------------------------------------------------

def _pool(action_delay_blocks: int = 0):
    return PoolConfig(
        address="0x" + "a" * 40, symbol="T/U",
        token0="0x" + "1" * 40, token1="0x" + "2" * 40,
        decimals0=18, decimals1=6, fee_tier=3000, tick_spacing=10,
        range=TickWidthRange(type="tick_width", width_ticks=200),
        position_size_usd=10_000, tx_cost_usd=0.0, slippage_bps=0,
        action_delay_blocks=action_delay_blocks,
    )


def _pred():
    return PredictionConfig(horizon_blocks=25, noise_sigma=0.0, vol_bucket_blocks=5)


def _swaps(n: int):
    sqrt_p = UniswapV3Math.get_sqrt_ratio_at_tick(0)
    return pd.DataFrame([{
        "block": 1_000_000 + i, "time": 1_700_000_000 + i,
        "tick": 0, "sqrt_price_x96": sqrt_p,
        "amount0": 10**17, "amount1": -10**17, "liquidity": 10**22,
    } for i in range(n)])


def test_delay_defers_action():
    """A strategy that EXITs at call 5 should only execute at call 5 + delay."""
    @register_strategy("_test_once_exit")
    class OnceExit(Strategy):
        def __init__(self):
            self.n = 0
        def decide(self, ctx: DecisionContext) -> StrategyAction:
            self.n += 1
            if self.n == 5:
                return StrategyAction.exit()
            return StrategyAction.hold()

    swaps = _swaps(60)
    strat = build_strategy("_test_once_exit")
    res = run_backtest(swaps, _pool(action_delay_blocks=10), _pred(), strat)
    exit_actions = [a for a in res.actions if a["action"] == "EXIT"]
    assert len(exit_actions) == 1
    exit_block = exit_actions[0]["block"]
    decided_at = exit_actions[0]["decided_at"]
    assert exit_block - decided_at == 10  # delay applied

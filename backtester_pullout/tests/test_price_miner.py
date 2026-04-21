"""Tests for price_miner strategy."""
import math

import pytest

from backtester_pullout.backtester.strategies import build_strategy, register_builtins
from backtester_pullout.backtester.strategies.base import ActionKind, DecisionContext

register_builtins()


def _ctx(current_tick=0, in_pos=False, position=None,
         predicted_return=0.0, tick_spacing=10, block=1_000_000, bslka=10_000):
    return DecisionContext(
        block=block, sqrt_price_x96=1 << 96,
        current_tick=current_tick,
        predicted_vol=0.0, realized_vol=0.0,
        predicted_return=predicted_return, realized_return=0.0,
        in_position=in_pos, blocks_since_last_action=bslka,
        inventory=(10**18, 10**18), position=position,
        tick_spacing=tick_spacing,
        default_range_fn=lambda t: (t - 100, t + 100),
    )


def test_initial_enter_centers_on_current_when_mu_zero():
    s = build_strategy("price_miner", {"min_width_spacings": 5,
                                        "rebalance_distance_ticks": 10})
    a = s.decide(_ctx(current_tick=1000, predicted_return=0.0, tick_spacing=10))
    assert a.kind is ActionKind.ENTER
    # Range centered on 1000 with min_width = 10 * 5 = 50, so [950, 1050]
    assert a.tick_lower == 950
    assert a.tick_upper == 1050


def test_initial_enter_leans_to_predicted_up():
    """Positive predicted return → target tick above current."""
    s = build_strategy("price_miner", {"min_width_spacings": 5,
                                        "rebalance_distance_ticks": 10})
    # log(1.01) ≈ 0.00995, ticks to move = 0.00995 / ln(1.0001) ≈ 99.5
    mu = math.log(1.01)
    a = s.decide(_ctx(current_tick=1000, predicted_return=mu, tick_spacing=10))
    assert a.kind is ActionKind.ENTER
    center = (a.tick_lower + a.tick_upper) // 2
    # Expect ~99.5 ticks up, snapped to spacing 10 → center near 1100
    assert 1090 <= center <= 1110


def test_initial_enter_leans_to_predicted_down():
    s = build_strategy("price_miner", {"min_width_spacings": 5,
                                        "rebalance_distance_ticks": 10})
    mu = math.log(0.99)  # negative
    a = s.decide(_ctx(current_tick=1000, predicted_return=mu, tick_spacing=10))
    assert a.kind is ActionKind.ENTER
    center = (a.tick_lower + a.tick_upper) // 2
    assert 890 <= center <= 910


def test_no_rebalance_when_target_within_tolerance():
    s = build_strategy("price_miner", {"min_width_spacings": 5,
                                        "rebalance_distance_ticks": 50})
    # Predicted return tiny → target center very close to current → HOLD
    mu = math.log(1.001)  # ~10 ticks worth
    a = s.decide(_ctx(current_tick=1000, in_pos=True, position=(950, 1050),
                      predicted_return=mu, tick_spacing=10))
    assert a.kind is ActionKind.HOLD


def test_rebalance_when_target_far():
    s = build_strategy("price_miner", {"min_width_spacings": 5,
                                        "rebalance_distance_ticks": 20})
    # Large predicted move → target far from current
    mu = math.log(1.05)  # ~500 ticks
    a = s.decide(_ctx(current_tick=1000, in_pos=True, position=(950, 1050),
                      predicted_return=mu, tick_spacing=10))
    assert a.kind is ActionKind.REBALANCE
    center = (a.tick_lower + a.tick_upper) // 2
    # Should be roughly 1000 + 500 = 1500 (±10)
    assert 1480 <= center <= 1520


def test_gating_prevents_rapid_decisions():
    s = build_strategy("price_miner", {"min_blocks_between_decisions": 100,
                                        "min_width_spacings": 5,
                                        "rebalance_distance_ticks": 10})
    a1 = s.decide(_ctx(current_tick=1000, predicted_return=math.log(1.05),
                       tick_spacing=10, block=1_000))
    assert a1.kind is ActionKind.ENTER
    # Follow-up within window → HOLD
    a2 = s.decide(_ctx(current_tick=1100, in_pos=True,
                       position=(a1.tick_lower, a1.tick_upper),
                       predicted_return=math.log(1.10),
                       tick_spacing=10, block=1_010))
    assert a2.kind is ActionKind.HOLD

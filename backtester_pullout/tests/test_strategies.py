"""Strategy plugin tests."""
import math

import pytest

from backtester_pullout.backtester.strategies import (
    build_strategy,
    list_strategies,
    register_builtins,
)
from backtester_pullout.backtester.strategies.base import (
    ActionKind,
    DecisionContext,
    StrategyAction,
)


register_builtins()


def _ctx(pv=0.01, rv=0.01, in_pos=True, bslka=10_000, block=1_000_000,
         current_tick=0, inventory=(10**18, 10**18), position=None,
         tick_spacing=10):
    return DecisionContext(
        block=block,
        sqrt_price_x96=1 << 96,
        current_tick=current_tick,
        predicted_vol=pv,
        realized_vol=rv,
        in_position=in_pos,
        blocks_since_last_action=bslka,
        inventory=inventory,
        position=position,
        tick_spacing=tick_spacing,
        default_range_fn=lambda t: (t - 100, t + 100),
    )


def test_all_builtins_registered():
    for name in ["always_in", "binary", "hysteresis", "graduated",
                 "min_hold", "time_gated", "hysteresis_with_holding"]:
        assert name in list_strategies()


def test_binary_threshold_exits_above():
    s = build_strategy("binary", {"threshold": 0.01})
    assert s.decide(_ctx(pv=0.02, in_pos=True)) .kind is ActionKind.EXIT
    assert s.decide(_ctx(pv=0.005, in_pos=False)) .kind is ActionKind.ENTER
    assert s.decide(_ctx(pv=0.005, in_pos=True)) .kind is ActionKind.HOLD


def test_binary_threshold_nan_holds():
    s = build_strategy("binary", {"threshold": 0.01})
    assert s.decide(_ctx(pv=float("nan"), in_pos=True)) .kind is ActionKind.HOLD


def test_hysteresis_bands():
    s = build_strategy("hysteresis", {"threshold_high": 0.02, "threshold_low": 0.005})
    # in position, vol in mid-band → HOLD (no exit until > high)
    assert s.decide(_ctx(pv=0.010, in_pos=True)) .kind is ActionKind.HOLD
    # in position, vol spikes → EXIT
    assert s.decide(_ctx(pv=0.025, in_pos=True)) .kind is ActionKind.EXIT
    # out of position, vol still in mid-band → HOLD (re-entry only below low)
    assert s.decide(_ctx(pv=0.010, in_pos=False)) .kind is ActionKind.HOLD
    # out of position, vol drops → ENTER
    assert s.decide(_ctx(pv=0.003, in_pos=False)) .kind is ActionKind.ENTER


def test_hysteresis_rejects_inverted_thresholds():
    with pytest.raises(ValueError):
        build_strategy("hysteresis", {"threshold_high": 0.01, "threshold_low": 0.02})


def test_graduated():
    s = build_strategy("graduated", {"low": 0.01, "high": 0.05})
    # pv=0.04 → f=0.75 > 0.5 → EXIT (avoiding float boundary issues at f=0.5)
    assert s.decide(_ctx(pv=0.04, in_pos=True)) .kind is ActionKind.EXIT
    # below low → f=0 → ENTER when out
    assert s.decide(_ctx(pv=0.005, in_pos=False)) .kind is ActionKind.ENTER
    # above high → f=1 → EXIT when in
    assert s.decide(_ctx(pv=0.10, in_pos=True)) .kind is ActionKind.EXIT


def test_min_hold_blocks_action_within_window():
    s = build_strategy("min_hold", {
        "inner": {"type": "binary", "params": {"threshold": 0.01}},
        "min_holding_blocks": 100,
    })
    # Would exit, but blocks_since_last_action too small
    assert s.decide(_ctx(pv=0.02, in_pos=True, bslka=10)) .kind is ActionKind.HOLD
    # Enough time elapsed
    assert s.decide(_ctx(pv=0.02, in_pos=True, bslka=200)) .kind is ActionKind.EXIT


def test_time_gated():
    s = build_strategy("time_gated", {
        "inner": {"type": "binary", "params": {"threshold": 0.01}},
        "decision_every_blocks": 100,
    })
    # first call — always allowed
    assert s.decide(_ctx(pv=0.02, in_pos=True, block=1_000_000)) .kind is ActionKind.EXIT
    # right after — gated off
    assert s.decide(_ctx(pv=0.02, in_pos=False, block=1_000_050)) .kind is ActionKind.HOLD
    # after window — allowed again
    assert s.decide(_ctx(pv=0.005, in_pos=False, block=1_000_110)) .kind is ActionKind.ENTER


def test_hysteresis_with_holding_combo():
    s = build_strategy("hysteresis_with_holding", {
        "threshold_high": 0.02,
        "threshold_low": 0.005,
        "min_holding_blocks": 50,
    })
    # Would exit, blocked by holding
    assert s.decide(_ctx(pv=0.03, in_pos=True, bslka=10)) .kind is ActionKind.HOLD
    # After holding window, allowed
    assert s.decide(_ctx(pv=0.03, in_pos=True, bslka=100)) .kind is ActionKind.EXIT

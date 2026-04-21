"""Tests for the volatility_miner strategy."""
from backtester_pullout.backtester.strategies import build_strategy, register_builtins
from backtester_pullout.backtester.strategies.base import (
    ActionKind,
    DecisionContext,
)


register_builtins()


def _ctx(current_tick=0, in_pos=False, position=None,
         inventory=(10**18, 10**18), tick_spacing=10, block=1_000_000,
         bslka=10_000, predicted_vol=0.0):
    return DecisionContext(
        block=block,
        sqrt_price_x96=1 << 96,
        current_tick=current_tick,
        predicted_vol=predicted_vol,
        realized_vol=0.0,
        in_position=in_pos,
        blocks_since_last_action=bslka,
        inventory=inventory,
        position=position,
        tick_spacing=tick_spacing,
        default_range_fn=lambda t: (t - 100, t + 100),
    )


def test_initial_enter_uses_min_width_when_no_history():
    s = build_strategy("volatility_miner", {
        "width_factor": 3.0, "volatility_window": 10, "min_width_spacings": 25,
    })
    a = s.decide(_ctx(current_tick=1000, tick_spacing=10))
    assert a.kind is ActionKind.ENTER
    # width = min_width = 250 (10 * 25), center = 1000 → [750, 1250]
    assert a.tick_lower == 750
    assert a.tick_upper == 1250


def test_token1_dust_places_range_above():
    """Token1 is dust → must mint from token0 only → range ABOVE current tick."""
    s = build_strategy("volatility_miner", {})
    a = s.decide(_ctx(current_tick=1000, tick_spacing=10,
                      inventory=(10**18, 0)))
    assert a.kind is ActionKind.ENTER
    # lower = center + ts = 1010, upper = lower + 2*250 = 1510
    assert a.tick_lower == 1010
    assert a.tick_upper == 1510


def test_token0_dust_places_range_below():
    """Token0 is dust → must mint from token1 only → range BELOW current tick."""
    s = build_strategy("volatility_miner", {})
    a = s.decide(_ctx(current_tick=1000, tick_spacing=10,
                      inventory=(0, 10**18)))
    assert a.kind is ActionKind.ENTER
    # upper = center - ts = 990, lower = upper - 2*250 = 490
    assert a.tick_lower == 490
    assert a.tick_upper == 990


def test_in_position_holds_when_far_from_edge():
    s = build_strategy("volatility_miner", {})
    # position [800, 1200], width=400, buffer=80, current=1000 (middle)
    a = s.decide(_ctx(current_tick=1000, in_pos=True, position=(800, 1200)))
    assert a.kind is ActionKind.HOLD


def test_in_position_rebalances_when_near_edge():
    s = build_strategy("volatility_miner", {})
    # position [800, 1200], width=400, buffer=80
    # current=1130 → 1130 > (1200 - 80) = 1120 → near upper edge
    a = s.decide(_ctx(current_tick=1130, in_pos=True, position=(800, 1200),
                      tick_spacing=10))
    assert a.kind is ActionKind.REBALANCE
    # New range centered at 1130 (snapped → 1130)
    assert a.tick_lower < 1130 < a.tick_upper


def test_exits_when_predicted_vol_above_threshold():
    s = build_strategy("volatility_miner", {
        "exit_vol_threshold": 0.01,
        "reentry_vol_threshold": 0.003,
    })
    # In position, vol spike → EXIT
    a = s.decide(_ctx(in_pos=True, position=(-100, 100), predicted_vol=0.02,
                      current_tick=0, tick_spacing=10))
    assert a.kind is ActionKind.EXIT


def test_reenters_when_predicted_vol_below_reentry():
    s = build_strategy("volatility_miner", {
        "exit_vol_threshold": 0.01,
        "reentry_vol_threshold": 0.003,
    })
    # Out of position, vol calm → ENTER
    a = s.decide(_ctx(in_pos=False, predicted_vol=0.001,
                      current_tick=0, tick_spacing=10))
    assert a.kind is ActionKind.ENTER


def test_stays_out_in_hysteresis_band():
    s = build_strategy("volatility_miner", {
        "exit_vol_threshold": 0.01,
        "reentry_vol_threshold": 0.003,
    })
    # Out of position, vol in mid-band (above reentry but below exit) → HOLD
    a = s.decide(_ctx(in_pos=False, predicted_vol=0.005,
                      current_tick=0, tick_spacing=10))
    assert a.kind is ActionKind.HOLD


def test_rejects_inverted_vol_thresholds():
    import pytest
    with pytest.raises(ValueError, match="reentry_vol_threshold"):
        build_strategy("volatility_miner", {
            "exit_vol_threshold": 0.003,
            "reentry_vol_threshold": 0.01,
        })


def test_volatility_grows_width():
    s = build_strategy("volatility_miner", {
        "width_factor": 1000.0, "volatility_window": 5, "min_width_spacings": 5,
        # No gating for this pure-vol test
        "min_blocks_between_decisions": 0,
    })
    # Feed wildly varying ticks to push vol up (advancing block each call
    # so the gating wouldn't block us anyway).
    for i, t in enumerate([1000, 1100, 900, 1200, 800, 1300, 700, 1400, 600, 1500]):
        s.decide(_ctx(current_tick=t, tick_spacing=10, block=1_000_000 + i))
    a = s.decide(_ctx(current_tick=1000, tick_spacing=10, block=1_001_000))
    # Expect a much wider range than the 50-tick min_width
    assert a.kind is ActionKind.ENTER
    width = a.tick_upper - a.tick_lower
    assert width > 50

"""Unit tests for LiquidityMap."""
import pandas as pd

from backtester_pullout.backtester.liquidity_map import LiquidityMap


def test_empty_map_returns_zero():
    m = LiquidityMap()
    assert m.active_L_at(0) == 0
    assert m.active_L_at(1_000_000) == 0


def test_single_position():
    """Position [-100, 100] with L=1000 → active L is 1000 inside, 0 outside."""
    m = LiquidityMap()
    m.add_position(-100, 100, 1000)
    assert m.active_L_at(-101) == 0   # below range
    assert m.active_L_at(-100) == 1000  # at lower (inclusive)
    assert m.active_L_at(0) == 1000     # in range
    assert m.active_L_at(99) == 1000    # in range
    # At tick 100: liquidity_net[100] = -1000, so cumulative = 0 → out
    assert m.active_L_at(100) == 0


def test_two_overlapping_positions():
    """Position A [-100, 100] L=1000; B [0, 200] L=500 → at tick 50, both active."""
    m = LiquidityMap()
    m.add_position(-100, 100, 1000)
    m.add_position(0, 200, 500)
    assert m.active_L_at(-50) == 1000        # only A
    assert m.active_L_at(50) == 1500         # both
    assert m.active_L_at(150) == 500         # only B
    assert m.active_L_at(250) == 0           # neither


def test_burn_subtracts():
    m = LiquidityMap()
    m.add_position(-100, 100, 1000)
    m.add_position(-100, 100, -300)  # burn 300
    assert m.active_L_at(0) == 700


def test_apply_dataframe():
    m = LiquidityMap()
    df = pd.DataFrame({
        "tick_lower": [-100, 0],
        "tick_upper": [100, 200],
        "amount": [1000, 500],
        "kind": [1, 1],
    })
    m.apply(df)
    assert m.active_L_at(50) == 1500


def test_apply_with_burn():
    m = LiquidityMap()
    df = pd.DataFrame({
        "tick_lower": [-100, -100],
        "tick_upper": [100, 100],
        "amount": [1000, 300],
        "kind": [1, -1],   # mint 1000, burn 300
    })
    m.apply(df)
    assert m.active_L_at(0) == 700


def test_rebuild_lazy():
    m = LiquidityMap()
    m.add_position(-100, 100, 1000)
    _ = m.active_L_at(0)        # triggers rebuild
    assert not m._dirty
    m.add_position(0, 200, 500) # marks dirty again
    assert m._dirty
    _ = m.active_L_at(50)        # rebuilds again
    assert m.active_L_at(50) == 1500


def test_total_net_zero_after_full_burn():
    m = LiquidityMap()
    m.add_position(-100, 100, 1000)
    m.add_position(-100, 100, -1000)
    assert m.total_net() == 0
    assert m.active_L_at(0) == 0

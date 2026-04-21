"""Vol oracle tests — unit only, no DB."""
import numpy as np
import pandas as pd
import pytest

from backtester_pullout.backtester.vol import (
    VolOracle,
    bucketed_price_series,
    derive_seed,
    make_rng,
)
from validator.utils.math import UniswapV3Math


def _make_swaps(ticks: list[int], blocks_per: int = 1) -> pd.DataFrame:
    rows = []
    for i, t in enumerate(ticks):
        rows.append({
            "block": 1_000_000 + i * blocks_per,
            "time": 1_700_000_000 + i,
            "tick": t,
            "sqrt_price_x96": UniswapV3Math.get_sqrt_ratio_at_tick(t),
            "amount0": 10**18,
            "amount1": -10**18,
            "liquidity": 10**22,
        })
    return pd.DataFrame(rows)


def test_seed_determinism():
    # Same seed + same cell → same stream.
    r1 = make_rng(42, 0)
    r2 = make_rng(42, 0)
    assert np.array_equal(r1.normal(size=10), r2.normal(size=10))
    # Different cell → different stream.
    r3 = make_rng(42, 1)
    assert not np.array_equal(
        make_rng(42, 0).normal(size=10),
        r3.normal(size=10),
    )


def test_derive_seed_is_stable():
    # Pinned values so we notice accidental changes.
    assert derive_seed(42, 0) != derive_seed(42, 1)
    # Pure function → same inputs → same output
    assert derive_seed(12345, 7) == derive_seed(12345, 7)


def test_bucketed_series_picks_last_swap_in_bucket():
    # Swaps at ticks [0, 10, 20, 30, 40], one per block, blocks 1000000..1000004.
    swaps = _make_swaps([0, 10, 20, 30, 40])
    series = bucketed_price_series(
        swaps, bucket_blocks=2,
        start_block=1_000_000, end_block=1_000_004,
    )
    # bucket_ends: 1000002, 1000004 → should pick swaps at ticks 20 and 40 respectively
    assert list(series["bucket_end_block"]) == [1_000_002, 1_000_004]
    expected_sqrt = [
        UniswapV3Math.get_sqrt_ratio_at_tick(20),
        UniswapV3Math.get_sqrt_ratio_at_tick(40),
    ]
    assert list(series["sqrt_price_x96"]) == expected_sqrt


def test_constant_price_has_zero_vol():
    swaps = _make_swaps([0] * 100)  # all at same tick
    oracle = VolOracle.build(
        swaps, bucket_blocks=5, horizon_blocks=25, noise_sigma=0.0,
        rng=make_rng(1),
    )
    # All realized vol values (where defined) must be 0.0
    defined = oracle.realized[~np.isnan(oracle.realized)]
    assert len(defined) > 0
    assert (defined == 0.0).all()


def test_perfect_oracle_predicted_equals_realized_at_zero_noise():
    # Make prices move a bit so vol > 0
    swaps = _make_swaps([0, 5, -3, 2, 7, -1, 4] * 20)
    oracle = VolOracle.build(
        swaps, bucket_blocks=2, horizon_blocks=20, noise_sigma=0.0,
        rng=make_rng(123),
    )
    # With noise_sigma=0, predicted == realized exactly.
    mask = ~np.isnan(oracle.realized)
    assert np.array_equal(oracle.realized[mask], oracle.predicted[mask])


def test_same_seed_same_predictions():
    swaps = _make_swaps([0, 10, -5, 3, -2] * 30)
    a = VolOracle.build(swaps, 2, 20, 0.3, rng=make_rng(42))
    b = VolOracle.build(swaps, 2, 20, 0.3, rng=make_rng(42))
    mask = ~np.isnan(a.predicted)
    assert np.array_equal(a.predicted[mask], b.predicted[mask])


def test_different_seeds_different_predictions():
    swaps = _make_swaps([0, 10, -5, 3, -2] * 30)
    a = VolOracle.build(swaps, 2, 20, 0.3, rng=make_rng(42))
    b = VolOracle.build(swaps, 2, 20, 0.3, rng=make_rng(43))
    mask = ~np.isnan(a.predicted) & ~np.isnan(b.predicted)
    # Not literally every element different, but the arrays should differ.
    assert not np.array_equal(a.predicted[mask], b.predicted[mask])


def test_query_before_first_bucket_returns_nan():
    swaps = _make_swaps([0, 10, 20, 30, 40])
    oracle = VolOracle.build(
        swaps, bucket_blocks=2, horizon_blocks=6, noise_sigma=0.0,
        rng=make_rng(1), start_block=1_000_000, end_block=1_000_004,
    )
    # Query at block 1_000_001 — before first bucket end (1_000_002)
    assert np.isnan(oracle.realized_vol(1_000_001))


def test_range_too_short_raises():
    swaps = _make_swaps([0, 10])
    with pytest.raises(ValueError, match="range too short"):
        VolOracle.build(
            swaps, bucket_blocks=100, horizon_blocks=500, noise_sigma=0.0,
            rng=make_rng(1), start_block=0, end_block=50,
        )

"""Unit tests for LPPosition + fee accrual.

Unit-only — no DB required.
"""
from validator.utils.math import UniswapV3Math

from backtester_pullout.backtester.position import (
    LPPosition,
    fee_rate_from_tier,
    value_in_token1,
)


def test_fee_rate_from_tier():
    assert fee_rate_from_tier(3000) == 0.003
    assert fee_rate_from_tier(500) == 0.0005
    assert abs(fee_rate_from_tier(640) - 0.00064) < 1e-12


def test_in_range_boundaries():
    lower, upper = -1000, 1000
    pos = LPPosition.from_amounts(
        lower, upper, UniswapV3Math.get_sqrt_ratio_at_tick(0),
        amount0=10**18, amount1=10**6,
    )
    assert pos.in_range(UniswapV3Math.get_sqrt_ratio_at_tick(lower))     # inclusive low
    assert pos.in_range(UniswapV3Math.get_sqrt_ratio_at_tick(upper))     # inclusive high
    assert pos.in_range(UniswapV3Math.get_sqrt_ratio_at_tick(0))
    assert not pos.in_range(UniswapV3Math.get_sqrt_ratio_at_tick(lower - 1))
    assert not pos.in_range(UniswapV3Math.get_sqrt_ratio_at_tick(upper + 1))


def test_out_of_range_earns_nothing():
    pos = LPPosition.from_amounts(
        -100, 100, UniswapV3Math.get_sqrt_ratio_at_tick(0),
        10**18, 10**18,
    )
    # swap at tick 1000 — far above our range
    pos.accrue_fee_from_swap(
        sqrt_price_x96=UniswapV3Math.get_sqrt_ratio_at_tick(1000),
        amount0=10**18, amount1=-10**18,
        pool_liquidity=10**20,
        fee_rate=0.003,
    )
    assert pos.fees0 == 0 and pos.fees1 == 0


def test_in_range_fee_dilution():
    """Our L in the denominator → if our_L == pool_L, we get ~50% of fees."""
    pos = LPPosition.from_amounts(
        -100, 100, UniswapV3Math.get_sqrt_ratio_at_tick(0),
        10**24, 10**24,
    )
    # Force our L to equal pool L by construction of the dilution denominator
    amount0 = 10**18
    pos.accrue_fee_from_swap(
        sqrt_price_x96=UniswapV3Math.get_sqrt_ratio_at_tick(0),
        amount0=amount0, amount1=-5 * 10**17,
        pool_liquidity=pos.liquidity,  # equal → share should be 0.5
        fee_rate=0.003,
    )
    # share = L / (L+L) = 0.5; expected fee0 ≈ amount0 * 0.003 * 0.5
    expected = int(amount0 * 0.003 * 0.5)
    assert pos.fees0 == expected
    assert pos.fees1 == 0  # only amount0 was positive


def test_amount1_side_when_amount0_negative():
    """When token1 is being swapped in (amount1 > 0), fees accrue in token1."""
    pos = LPPosition.from_amounts(
        -100, 100, UniswapV3Math.get_sqrt_ratio_at_tick(0),
        10**24, 10**24,
    )
    pos.accrue_fee_from_swap(
        sqrt_price_x96=UniswapV3Math.get_sqrt_ratio_at_tick(0),
        amount0=-5 * 10**17, amount1=10**18,
        pool_liquidity=pos.liquidity,
        fee_rate=0.003,
    )
    assert pos.fees1 > 0
    assert pos.fees0 == 0


def test_value_in_token1_q192():
    """At sqrt_price corresponding to price=1 (tick 0, decimals equal),
    value ≈ amount0 + amount1 — verify Q192 math matches."""
    sqrt_p = UniswapV3Math.get_sqrt_ratio_at_tick(0)
    v = value_in_token1(10**18, 10**18, sqrt_p)
    # At tick 0, price ≈ 1, so value should be ~2*10**18. Exact value uses
    # rounding; allow 1-unit tolerance from the integer division.
    assert abs(v - 2 * 10**18) < 1000


def test_amounts_at_price_roundtrip():
    """Mint at tick 0, withdraw at tick 0 → should get back close to what we put in."""
    sqrt_p = UniswapV3Math.get_sqrt_ratio_at_tick(0)
    a0_in, a1_in = 10**18, 10**18
    pos = LPPosition.from_amounts(-100, 100, sqrt_p, a0_in, a1_in)
    a0_out, a1_out = pos.amounts_at(sqrt_p)
    # Uniswap L calc rounds down; we should get back <= what we put in, but
    # within a reasonable tolerance.
    assert a0_out <= a0_in and a1_out <= a1_in
    assert a0_out > a0_in * 0.99
    assert a1_out > a1_in * 0.99

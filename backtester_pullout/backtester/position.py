"""LP position model with fee accrual matching validator/services/backtester.py.

Valuation is in **token1 units** using Q192 fixed-point math — same as the
reference validator backtester:
    value_token1 = (amount0 * sqrt_price_x96^2) // Q192 + amount1

Fee accrual formula (per swap, in-range only):
    active_L_with_us = swap.liquidity + our_L     # dilution
    share = our_L / active_L_with_us              # capped at 1.0
    if amount0 > 0: fees0 += amount0 * fee_rate * share
    elif amount1 > 0: fees1 += amount1 * fee_rate * share

This matches validator/services/backtester.py:67,126-133 exactly.

Tick math is reused from validator/utils/math.py via import (read-only use).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from validator.utils.math import UniswapV3Math


def fee_rate_from_tier(fee_tier: int) -> float:
    """Uniswap/Aerodrome fee-tier convention: 1 tier unit = 1 pip = 1e-6.

    e.g. fee_tier=3000 → 0.003 (0.3%);  fee_tier=640 → 0.00064 (0.064%).
    """
    return fee_tier / 1_000_000.0


@dataclass
class LPPosition:
    """A concentrated-liquidity position: L units of liquidity in [tick_lower, tick_upper].

    Attributes `sqrt_price_lower_x96` and `sqrt_price_upper_x96` are precomputed
    once at construction — the in-range check is millions-of-swaps hot.

    `fees0` and `fees1` accumulate in token0 / token1 raw units (no decimals).
    """
    tick_lower: int
    tick_upper: int
    liquidity: int  # L
    sqrt_price_lower_x96: int
    sqrt_price_upper_x96: int
    fees0: int = 0
    fees1: int = 0

    @classmethod
    def from_amounts(
        cls,
        tick_lower: int,
        tick_upper: int,
        sqrt_price_x96: int,
        amount0: int,
        amount1: int,
    ) -> "LPPosition":
        """Mint a position at current price from raw token amounts.

        L is computed per Uniswap V3 spec; *used* amounts may be less than
        provided (the excess goes back as idle tokens — tracked by the engine,
        not this class).
        """
        L, _used0, _used1 = UniswapV3Math.position_liquidity_and_used_amounts(
            tick_lower, tick_upper, sqrt_price_x96, amount0, amount1,
        )
        return cls(
            tick_lower=tick_lower,
            tick_upper=tick_upper,
            liquidity=L,
            sqrt_price_lower_x96=UniswapV3Math.get_sqrt_ratio_at_tick(tick_lower),
            sqrt_price_upper_x96=UniswapV3Math.get_sqrt_ratio_at_tick(tick_upper),
        )

    def in_range(self, sqrt_price_x96: int) -> bool:
        return self.sqrt_price_lower_x96 <= sqrt_price_x96 <= self.sqrt_price_upper_x96

    def amounts_at(self, sqrt_price_x96: int) -> Tuple[int, int]:
        """Held (token0, token1) at a given price, not including accrued fees."""
        return UniswapV3Math.get_amounts_for_liquidity(
            sqrt_price_x96,
            self.sqrt_price_lower_x96,
            self.sqrt_price_upper_x96,
            self.liquidity,
        )

    def accrue_fee_from_swap(
        self,
        sqrt_price_x96: int,
        amount0: int,
        amount1: int,
        pool_liquidity: int,
        fee_rate: float,
    ) -> None:
        """If in-range at this sqrt_price, accrue fees from one swap.

        Matches validator/services/backtester.py fee attribution bit-for-bit:
        - dilution: our L is added to the denominator
        - fee side: whichever of amount0 / amount1 is positive (token in)
        """
        if not self.in_range(sqrt_price_x96):
            return

        active = int(pool_liquidity) + self.liquidity
        if active <= 0:
            return

        # share capped at 1.0, matches reference (handles sparse-pool edge case)
        share = min(1.0, self.liquidity / active)

        if amount0 > 0:
            self.fees0 += int(int(amount0) * fee_rate * share)
        elif amount1 > 0:
            self.fees1 += int(int(amount1) * fee_rate * share)


def value_in_token1(
    amount0: int,
    amount1: int,
    sqrt_price_x96: int,
) -> int:
    """Q192 token1-denominated valuation. Matches validator backtester.

    value = amount0 * sqrt_price² // Q192  +  amount1
    """
    price_x192 = sqrt_price_x96 * sqrt_price_x96
    return (int(amount0) * price_x192) // UniswapV3Math.Q192 + int(amount1)


def simulate_swap_v3(
    amount_in: int,
    side: int,                  # 0 = sell token0 for token1; 1 = sell token1 for token0
    sqrt_price_x96: int,
    active_L: int,
    pool_fee_tier: int,         # pip units (e.g. 500 = 0.05%)
    extra_slippage_bps: int = 0,
) -> tuple[int, int]:
    """Simulate a Uniswap V3 single-tick swap, returning (amount_out, new_sqrt).

    Models three cost components realistically:
      1. Pool fee — taken off the input: `dx_effective = dx * (1 - fee_rate)`
      2. Price impact — from moving the sqrt-price along the V3 curve at
         `active_L`. Larger swap or thinner pool → more impact.
      3. Optional `extra_slippage_bps` — applied to the output for modeling
         non-idealities (MEV, cross-tick losses we don't model, etc).

    Single-tick approximation: assumes active_L doesn't change during the swap.
    For small swaps relative to L this is exact; for large swaps that cross
    ticks, this UNDERESTIMATES slippage. Acceptable for rebalancing-size swaps.
    """
    if amount_in <= 0 or active_L <= 0:
        return 0, sqrt_price_x96

    Q96 = UniswapV3Math.Q96
    fee_rate_bps = pool_fee_tier // 100  # e.g. 500 -> 5 bps
    # Input after pool fee
    dx_net = amount_in - (amount_in * fee_rate_bps) // 10_000

    if side == 0:
        # Sell token0 → price goes DOWN
        # new_sqrt = L*Q96*sqrt / (L*Q96 + dx*sqrt)
        num = active_L * Q96 * sqrt_price_x96
        den = active_L * Q96 + dx_net * sqrt_price_x96
        if den == 0:
            return 0, sqrt_price_x96
        new_sqrt = num // den
        # dy = L * (sqrt - new_sqrt) / Q96
        amount_out = (active_L * (sqrt_price_x96 - new_sqrt)) // Q96
    else:
        # Sell token1 → price goes UP
        # new_sqrt = sqrt + dx * Q96 / L
        new_sqrt = sqrt_price_x96 + (dx_net * Q96) // active_L
        # dy0 = L * Q96 * (new_sqrt - sqrt) / (sqrt * new_sqrt)
        if sqrt_price_x96 == 0 or new_sqrt == 0:
            return 0, sqrt_price_x96
        amount_out = (active_L * Q96 * (new_sqrt - sqrt_price_x96)) // (sqrt_price_x96 * new_sqrt // Q96) // Q96
        # ^ the division chain above has been split to avoid Q192 overflow in the intermediate
        # Let me redo more carefully:
        # dy0 = L * Q96 * (new_sqrt - sqrt) / (sqrt * new_sqrt)
        # = (L * Q96 * (new_sqrt - sqrt)) // sqrt // new_sqrt
        amount_out = (active_L * Q96 * (new_sqrt - sqrt_price_x96)) // sqrt_price_x96 // new_sqrt

    amount_out = max(0, amount_out)
    # Extra slippage on output
    if extra_slippage_bps > 0:
        amount_out -= (amount_out * extra_slippage_bps) // 10_000
    return int(amount_out), int(new_sqrt)


def swap_to_ratio(
    amount0: int,
    amount1: int,
    sqrt_price_x96: int,
    tick_lower: int,
    tick_upper: int,
    active_L: int = 0,
    pool_fee_tier: int = 0,
    extra_slippage_bps: int = 0,
) -> tuple[int, int, int, int]:
    """Swap (amount0, amount1) to the ratio required by an LP range at current price.

    Realism knobs:
      - `active_L > 0` + `pool_fee_tier > 0` → proper V3 swap simulation via
        `simulate_swap_v3`. Output is reduced by pool fee AND price impact.
      - `active_L == 0` → fall back to idealized mid-price swap (no impact).
      - `extra_slippage_bps` → flat bps markup on top (for non-idealities).

    Returns (new_amount0, new_amount1, swap_amount_in, swap_side).
      swap_side: 0 if sold token0, 1 if sold token1, -1 if no swap.
      swap_amount_in: absolute amount of the SOLD token.
    """
    sqrtA = UniswapV3Math.get_sqrt_ratio_at_tick(tick_lower)
    sqrtB = UniswapV3Math.get_sqrt_ratio_at_tick(tick_upper)
    if sqrtA > sqrtB:
        sqrtA, sqrtB = sqrtB, sqrtA

    Q192 = UniswapV3Math.Q192
    P = int(sqrt_price_x96)
    price_x192 = P * P
    a0 = int(amount0)
    a1 = int(amount1)

    def _sell_t0_for_t1(qty: int) -> int:
        if active_L > 0 and pool_fee_tier > 0:
            out, _ = simulate_swap_v3(qty, side=0, sqrt_price_x96=P,
                                      active_L=active_L, pool_fee_tier=pool_fee_tier,
                                      extra_slippage_bps=extra_slippage_bps)
            return out
        # Idealized fallback
        delta = (qty * price_x192) // Q192
        delta -= (delta * extra_slippage_bps) // 10_000
        return max(0, delta)

    def _sell_t1_for_t0(qty: int) -> int:
        if active_L > 0 and pool_fee_tier > 0:
            out, _ = simulate_swap_v3(qty, side=1, sqrt_price_x96=P,
                                      active_L=active_L, pool_fee_tier=pool_fee_tier,
                                      extra_slippage_bps=extra_slippage_bps)
            return out
        if price_x192 == 0:
            return 0
        delta = (qty * Q192) // price_x192
        delta -= (delta * extra_slippage_bps) // 10_000
        return max(0, delta)

    # Case: price below range → all token0
    if P <= sqrtA:
        if a1 > 0:
            recv = _sell_t1_for_t0(a1)
            return a0 + recv, 0, a1, 1
        return a0, 0, 0, -1

    # Case: price above range → all token1
    if P >= sqrtB:
        if a0 > 0:
            recv = _sell_t0_for_t1(a0)
            return 0, a1 + recv, a0, 0
        return 0, a1, 0, -1

    # Case: in range — use Uniswap math to find required ratio at a reference L
    ref_L = 10 ** 30
    r0, r1 = UniswapV3Math.get_amounts_for_liquidity(P, sqrtA, sqrtB, ref_L)
    if r0 == 0 and r1 == 0:
        return a0, a1, 0, -1

    # Total value in token1 units
    V_t1 = (a0 * price_x192) // Q192 + a1
    if V_t1 <= 0:
        return a0, a1, 0, -1

    # Solve for target amounts preserving value:
    #   V_t1 = target_a0 * P²/Q192 + target_a1  with  target_a1/target_a0 = r1/r0
    # => target_a0 = V_t1 * Q192 * r0 / (P² * r0 + r1 * Q192)
    denom = price_x192 * r0 + r1 * Q192
    if denom == 0:
        return a0, a1, 0, -1
    target_a0 = (V_t1 * Q192 * r0) // denom
    target_a1 = V_t1 - (target_a0 * price_x192) // Q192  # re-derive a1 so value matches

    if a0 > target_a0:
        # Surplus token0 → sell for token1
        delta_t0 = a0 - target_a0
        recv = _sell_t0_for_t1(delta_t0)
        return target_a0, a1 + recv, delta_t0, 0
    elif a1 > target_a1:
        # Surplus token1 → sell for token0
        delta_t1 = a1 - target_a1
        recv = _sell_t1_for_t0(delta_t1)
        return a0 + recv, target_a1, delta_t1, 1

    # Already balanced (degenerate)
    return a0, a1, 0, -1

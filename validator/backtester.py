"""
Backtester for simulating LP strategy performance.

This module provides accurate simulation of Uniswap V3 / Aerodrome v3
concentrated liquidity positions, including:
- Proper fee calculation based on liquidity share
- Accurate impermanent loss computation
- Rebalance simulation following strategy rules
"""
import logging
import math
from typing import List, Dict, Any, Tuple, Optional, TYPE_CHECKING

from protocol import Strategy, Position, PerformanceMetrics, RebalanceRule, Inventory
from validator.database import DataSource

# Avoid circular import
if TYPE_CHECKING:
    from validator.validator import SN98Validator

logger = logging.getLogger(__name__)

# Default fee tiers for Aerodrome/Uniswap V3 pools
FEE_TIERS = {
    100: 0.0001,  # 0.01%
    500: 0.0005,  # 0.05%
    3000: 0.003,  # 0.3%
    10000: 0.01,  # 1%
}
DEFAULT_FEE_RATE = 0.003  # 0.3% default


class UniswapV3Math:
    """Uniswap V3 math utilities for liquidity calculations."""

    # Constants
    Q96 = 2**96
    MIN_TICK = -887272
    MAX_TICK = 887272

    @staticmethod
    def get_sqrt_ratio_at_tick(tick: int) -> int:
        """Calculate sqrtPriceX96 from tick."""
        # Clamp tick to valid range
        tick = max(UniswapV3Math.MIN_TICK, min(UniswapV3Math.MAX_TICK, tick))
        return int(1.0001 ** (tick / 2) * UniswapV3Math.Q96)

    @staticmethod
    def get_tick_at_sqrt_ratio(sqrt_price_x96: int) -> int:
        """Calculate tick from sqrtPriceX96."""
        if sqrt_price_x96 <= 0:
            return UniswapV3Math.MIN_TICK
        price = (sqrt_price_x96 / UniswapV3Math.Q96) ** 2
        if price <= 0:
            return UniswapV3Math.MIN_TICK
        return int(math.log(price) / math.log(1.0001))

    @staticmethod
    def tick_to_price(tick: int) -> float:
        """Convert tick to price (token1/token0)."""
        return 1.0001**tick

    @staticmethod
    def price_to_tick(price: float) -> int:
        """Convert price to tick."""
        if price <= 0:
            return UniswapV3Math.MIN_TICK
        return int(math.log(price) / math.log(1.0001))

    @staticmethod
    def get_liquidity_for_amounts(
        sqrt_price_x96: int,
        sqrt_price_a_x96: int,
        sqrt_price_b_x96: int,
        amount0: int,
        amount1: int,
    ) -> int:
        """
        Calculate liquidity from token amounts and price range.
        Uses the standard Uniswap V3 liquidity calculation.
        """
        # Ensure a < b
        if sqrt_price_a_x96 > sqrt_price_b_x96:
            sqrt_price_a_x96, sqrt_price_b_x96 = sqrt_price_b_x96, sqrt_price_a_x96

        if sqrt_price_x96 <= sqrt_price_a_x96:
            # Price below range - all liquidity in token0
            if sqrt_price_b_x96 == sqrt_price_a_x96:
                return 0
            liquidity = (amount0 * sqrt_price_a_x96 * sqrt_price_b_x96) // (
                (sqrt_price_b_x96 - sqrt_price_a_x96) * UniswapV3Math.Q96
            )
        elif sqrt_price_x96 < sqrt_price_b_x96:
            # Price in range - liquidity in both tokens
            liquidity0 = (
                (amount0 * sqrt_price_x96 * sqrt_price_b_x96)
                // ((sqrt_price_b_x96 - sqrt_price_x96) * UniswapV3Math.Q96)
                if sqrt_price_b_x96 > sqrt_price_x96
                else 0
            )

            liquidity1 = (
                (amount1 * UniswapV3Math.Q96) // (sqrt_price_x96 - sqrt_price_a_x96)
                if sqrt_price_x96 > sqrt_price_a_x96
                else 0
            )

            # Use minimum to ensure we don't exceed either token
            liquidity = (
                min(liquidity0, liquidity1)
                if liquidity0 > 0 and liquidity1 > 0
                else max(liquidity0, liquidity1)
            )
        else:
            # Price above range - all liquidity in token1
            if sqrt_price_b_x96 == sqrt_price_a_x96:
                return 0
            liquidity = (amount1 * UniswapV3Math.Q96) // (
                sqrt_price_b_x96 - sqrt_price_a_x96
            )

        return max(0, liquidity)

    @staticmethod
    def get_amounts_for_liquidity(
        sqrt_price_x96: int,
        sqrt_price_a_x96: int,
        sqrt_price_b_x96: int,
        liquidity: int,
    ) -> Tuple[int, int]:
        """
        Calculate token amounts from liquidity and price range.
        Returns (amount0, amount1).
        """
        # Ensure a < b
        if sqrt_price_a_x96 > sqrt_price_b_x96:
            sqrt_price_a_x96, sqrt_price_b_x96 = sqrt_price_b_x96, sqrt_price_a_x96

        if liquidity <= 0:
            return (0, 0)

        if sqrt_price_x96 <= sqrt_price_a_x96:
            # Price below range - all in token0
            if sqrt_price_a_x96 == 0 or sqrt_price_b_x96 == 0:
                return (0, 0)
            amount0 = (
                liquidity * (sqrt_price_b_x96 - sqrt_price_a_x96) * UniswapV3Math.Q96
            ) // (sqrt_price_a_x96 * sqrt_price_b_x96)
            amount1 = 0
        elif sqrt_price_x96 < sqrt_price_b_x96:
            # Price in range
            if sqrt_price_x96 == 0 or sqrt_price_b_x96 == 0:
                return (0, 0)
            amount0 = (
                liquidity * (sqrt_price_b_x96 - sqrt_price_x96) * UniswapV3Math.Q96
            ) // (sqrt_price_x96 * sqrt_price_b_x96)
            amount1 = (
                liquidity * (sqrt_price_x96 - sqrt_price_a_x96)
            ) // UniswapV3Math.Q96
        else:
            # Price above range - all in token1
            amount0 = 0
            amount1 = (
                liquidity * (sqrt_price_b_x96 - sqrt_price_a_x96)
            ) // UniswapV3Math.Q96

        return (max(0, amount0), max(0, amount1))

    @staticmethod
    def calculate_position_liquidity_and_amounts(
        position: Position, current_price: float
    ) -> Tuple[float, float, float]:
        """
        Calculate the liquidity value and ACTUAL amounts used for a position.

        In Uniswap V3, when you provide tokens, only the amount that fits
        the limiting token is actually deployed. The excess is not used.

        Args:
            position: LP position
            current_price: Current price (token1/token0)

        Returns:
            Tuple of (liquidity, actual_amount0_used, actual_amount1_used)
        """
        price_lower = UniswapV3Math.tick_to_price(position.tick_lower)
        price_upper = UniswapV3Math.tick_to_price(position.tick_upper)

        sqrt_price = math.sqrt(current_price)
        sqrt_price_lower = math.sqrt(price_lower)
        sqrt_price_upper = math.sqrt(price_upper)

        initial_amount0 = int(position.allocation0)
        initial_amount1 = int(position.allocation1)

        liquidity = 0.0
        actual_amount0 = 0.0
        actual_amount1 = 0.0

        # Calculate liquidity based on current price relative to range
        if current_price <= price_lower:
            # All in token0
            if sqrt_price_upper > sqrt_price_lower:
                liquidity = initial_amount0 * sqrt_price_lower * sqrt_price_upper / (
                    sqrt_price_upper - sqrt_price_lower
                )
                actual_amount0 = initial_amount0
                actual_amount1 = 0.0
            else:
                liquidity = 0
        elif current_price >= price_upper:
            # All in token1
            if sqrt_price_upper > sqrt_price_lower:
                liquidity = initial_amount1 / (sqrt_price_upper - sqrt_price_lower)
                actual_amount0 = 0.0
                actual_amount1 = initial_amount1
            else:
                liquidity = 0
        else:
            # In range - calculate from both sides and use minimum
            if sqrt_price_upper > sqrt_price:
                liquidity0 = initial_amount0 * sqrt_price * sqrt_price_upper / (
                    sqrt_price_upper - sqrt_price
                )
            else:
                liquidity0 = 0

            if sqrt_price > sqrt_price_lower:
                liquidity1 = initial_amount1 / (sqrt_price - sqrt_price_lower)
            else:
                liquidity1 = 0

            # Use minimum (limiting factor)
            if liquidity0 > 0 and liquidity1 > 0:
                liquidity = min(liquidity0, liquidity1)
            else:
                liquidity = max(liquidity0, liquidity1)

            # Calculate actual amounts used based on the chosen liquidity
            # amount0 = L * (sqrt_upper - sqrt_price) / (sqrt_price * sqrt_upper)
            # amount1 = L * (sqrt_price - sqrt_lower)
            if liquidity > 0:
                actual_amount0 = liquidity * (sqrt_price_upper - sqrt_price) / (
                    sqrt_price * sqrt_price_upper
                )
                actual_amount1 = liquidity * (sqrt_price - sqrt_price_lower)
            else:
                actual_amount0 = 0.0
                actual_amount1 = 0.0

        return (max(0.0, liquidity), max(0.0, actual_amount0), max(0.0, actual_amount1))


class Backtester:
    """
    Simulates LP strategy performance using historical pool events.
    Compares strategy performance against HODL baseline.

    Key improvements over naive implementation:
    - Calculates actual liquidity share per swap event
    - Simulates rebalances based on strategy rules
    - Uses pool-specific fee rates
    """

    def __init__(
        self,
        data_source: DataSource,
    ):
        """
        Initialize backtester.

        Args:
            data_source: Data source for historical data (implements DataSource interface)
        """
        self.db = data_source  # Keep as self.db for compatibility
        self.math = UniswapV3Math()

    @staticmethod
    def score_pol_strategy(
        amount0_raw: int,
        amount1_raw: int,
        fees_0: int,
        fees_1: int,
        token0_decimals: int,
        token1_decimals: int,
        final_price: float,
        target_ratio: float = 0.5,
        ratio_penalty_power: float = 2.0,
        fee_weight: float = 0.1,
    ) -> float:
        """
        Score a strategy where all values are normalized by token decimals.
        Higher score = better strategy.
        """

        # --- Normalize by decimals ---
        amount0 = amount0_raw / (10 ** token0_decimals)
        amount1 = amount1_raw / (10 ** token1_decimals)
        # --- Normalize & convert fees ---
        fees0 = fees_0 / (10 ** token0_decimals)
        fees1 = fees_1 / (10 ** token1_decimals)

        fees_collected = fees0 * final_price + fees1

        total_value = amount0 + amount1
        if total_value <= 0:
            return float("-inf")

        # --- Balance penalty ---
        actual_ratio = amount0 / total_value
        ratio_error = abs(actual_ratio - target_ratio) / target_ratio

        ratio_penalty = 1 / (1 + ratio_error ** ratio_penalty_power)

        # --- Final score ---
        score = (total_value * ratio_penalty) + (fee_weight * fees_collected)

        return score


    async def calculate_hodl_baseline(
        self,
        pair_address: str,
        initial_amount0: int,
        initial_amount1: int,
        start_block: int,
        end_block: int,
    ) -> float:
        """
        Calculate the value of simply holding the tokens (HODL).

        Args:
            pair_address: Pool address
            initial_amount0: Initial amount of token0
            initial_amount1: Initial amount of token1
            start_block: Starting block
            end_block: Ending block

        Returns:
            Final value in terms of token1
        """
        start_price = await self.db.get_price_at_block(pair_address, start_block)
        end_price = await self.db.get_price_at_block(pair_address, end_block)

        if start_price is None or end_price is None:
            logger.warning("Could not fetch prices for HODL baseline, using fallback")
            # Fallback: assume no price change
            start_price = start_price or 1.0
            end_price = end_price or start_price

        # Final value in token1 terms (tokens unchanged, just price differs)
        final_value = initial_amount0 * end_price + initial_amount1
        return final_value

    def _calculate_liquidity_share(
        self,
        simulated_in_range_liquidity: float,
        event: Dict[str, Any],
    ) -> float:
        """
        Calculate the share of fees this position earns from a swap.

        This is the key improvement: instead of assuming 1% share,
        we calculate the actual share based on:
        1. Position liquidity
        2. Total pool liquidity (from swap event)
        3. Whether price is in range

        Args:
            simulated_in_range_liquidity: Liquidity of the positions in range
            event: Swap event data

        Returns:
            Liquidity share (0.0 to 1.0)
        """
        # Get total pool liquidity from event (if available)
        pool_liquidity = event.get("liquidity")
        if pool_liquidity:
            pool_liquidity = float(pool_liquidity) + simulated_in_range_liquidity
        else:
            raise ValueError(f"Liquidity not available for event ${event.get('id')}")

        if pool_liquidity <= 0:
            logger.warning(
                f"Pool liquidity is <= 0 ({pool_liquidity}). "
                "This suggests bad data or a bug. Returning 0 share."
            )
            return 0.0

        # Calculate share (capped at 100% to handle edge cases)
        share = min(1.0, simulated_in_range_liquidity / pool_liquidity)
        return share

    def get_token_decimals(self, pair_address: str) -> Tuple[int, int]:
        """Get the token decimals"""


    async def evaluate_positions_performance(
        self,
        pair_address: str,
        rebalance_history: List[Dict[str, Any]],
        start_block: int,
        end_block: int,
        initial_inventory: Inventory,
        fee_rate: float,
    ) -> Dict[str, Any]:
        """
        Simulate a single LP position over a block range using V3 concentrated liquidity math.

        Args:
            pair_address: Pool address
            rebalance_history: LP positions to simulate
            start_block: Starting block
            end_block: Ending block
            initial_inventory: The inventory at the start of the simulation.
            fee_rate: Fee rate for the pool

        Returns:
            Dictionary containing:
            - fees_collected: Total fees earned (in token1 terms)
            - final_amount0: Amount of token0 at end
            - final_amount1: Amount of token1 at end
            - impermanent_loss: IL as fraction (0.0 to 1.0)
            - fees0: Fees in token0
            - fees1: Fees in token1
            - in_range_ratio: Fraction of time price was in range
        """

        rebalance_history.sort(key=lambda x: x["block"], reverse=True)

        def get_deployed_positions(current_block: int) -> List[Position]:
            """Get deployed positions for current block."""
            for rebalance in rebalance_history:
                if current_block > rebalance["block"]:
                    return rebalance["new_positions"]

            raise ValueError("Invalid rebalance history.")

        # Get swap events in this range
        swap_events = await self.db.get_swap_events(pair_address, start_block, end_block)

        # Track fees
        total_fees0 = 0.0
        total_fees1 = 0.0
        in_range_count = 0
        total_swaps = len(swap_events)

        # Simulate each swap for fee accumulation
        for event in swap_events:
            # Calculate price from sqrt_price_x96 if available
            sqrt_price_x96 = event.get("sqrt_price_x96")
            sqrt_price = int(sqrt_price_x96)
            event_price = (sqrt_price / (2 ** 96)) ** 2

            block_number = event.get("evt_block_number")
            positions = get_deployed_positions(block_number)
            total_in_range_liq = 0
            for position in positions:
                # Convert tick bounds to prices
                price_lower = self.math.tick_to_price(position.tick_lower)
                price_upper = self.math.tick_to_price(position.tick_upper)

                # Check if position is in range
                if price_lower <= event_price <= price_upper:
                    in_range_count += 1
                    # Calculate position liquidity AND actual amounts deployed
                    # In V3, you can't always deploy all tokens - only what fits the limiting token
                    (
                        position_liquidity,
                        amount0_deployed,
                        amount1_deployed,
                    ) = self.math.calculate_position_liquidity_and_amounts(position, event_price)
                    total_in_range_liq += position_liquidity

            # Get swap amounts (signed: positive = token came IN, negative = token went OUT)
            # In Uniswap V3, fees are ONLY charged on the INPUT token
            raw_amount0 = float(event.get("amount0", 0) or 0)
            raw_amount1 = float(event.get("amount1", 0) or 0)

            # Calculate liquidity share for this swap
            liquidity_share = self._calculate_liquidity_share(
                total_in_range_liq,
                event,
            )

            # Fees earned ONLY on the input token (the one with positive amount)
            # If amount0 > 0: user swapped token0 for token1, fee is on token0
            # If amount1 > 0: user swapped token1 for token0, fee is on token1
            if raw_amount0 > 0:
                total_fees0 += raw_amount0 * fee_rate * liquidity_share
            elif raw_amount1 > 0:
                total_fees1 += raw_amount1 * fee_rate * liquidity_share

        final_price = await self.db.get_price_at_block(pair_address, end_block)

        # get amounts currently in pool
        amount0_deployed, amount1_deployed = 0, 0
        for position in rebalance_history[0]["new_positions"]:
            _, amount0, amount1 = self.math.calculate_position_liquidity_and_amounts(position, final_price)
            amount0_deployed += int(amount0)
            amount1_deployed += int(amount1)

        final_inventory: Inventory = rebalance_history[0]["current_inventory"]

        # Calculate IL: compare LP value vs HODL value
        # IMPORTANT: Use ACTUAL deployed amounts for HODL baseline, not allocations!
        # The excess tokens are held and don't experience IL
        hodl_value_deployed = float(initial_inventory.amount0) * final_price + float(initial_inventory.amount1)
        amount0_holdings = amount0_deployed + int(final_inventory.amount0)
        amount1_holdings = amount1_deployed + int(final_inventory.amount1)
        lp_value_deployed = amount1_holdings * final_price + amount0_holdings

        # IL is only on the deployed portion
        if hodl_value_deployed > 0:
            impermanent_loss = max(
                0.0, (hodl_value_deployed - lp_value_deployed) / hodl_value_deployed
            )
        else:
            impermanent_loss = 0.0

        # Calculate in-range ratio
        in_range_ratio = in_range_count / total_swaps if total_swaps > 0 else 0.0
        fees_collected = total_fees0 * final_price + total_fees1
        return {
            "fees_collected": fees_collected,
            "impermanent_loss": impermanent_loss,
            "fees0": total_fees0,
            "fees1": total_fees1,
            "in_range_ratio": in_range_ratio,
            "amount0_deployed": amount0_deployed,
            "amount1_deployed": amount1_deployed,
            "amount0_holdings": amount0_holdings,
            "amount1_holdings": amount1_holdings,
        }

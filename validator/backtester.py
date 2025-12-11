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

from protocol import Strategy, Position, PerformanceMetrics, RebalanceRule
from validator.database import DataSource

# Avoid circular import
if TYPE_CHECKING:
    from validator.validator import SN98Validator

logger = logging.getLogger(__name__)

# Default fee tiers for Aerodrome/Uniswap V3 pools
FEE_TIERS = {
    100: 0.0001,    # 0.01%
    500: 0.0005,    # 0.05%
    3000: 0.003,    # 0.3%
    10000: 0.01,    # 1%
}
DEFAULT_FEE_RATE = 0.003  # 0.3% default


class UniswapV3Math:
    """Uniswap V3 math utilities for liquidity calculations."""

    # Constants
    Q96 = 2 ** 96
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
        return 1.0001 ** tick

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
        amount1: int
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
            liquidity0 = (amount0 * sqrt_price_x96 * sqrt_price_b_x96) // (
                (sqrt_price_b_x96 - sqrt_price_x96) * UniswapV3Math.Q96
            ) if sqrt_price_b_x96 > sqrt_price_x96 else 0

            liquidity1 = (amount1 * UniswapV3Math.Q96) // (
                sqrt_price_x96 - sqrt_price_a_x96
            ) if sqrt_price_x96 > sqrt_price_a_x96 else 0

            # Use minimum to ensure we don't exceed either token
            liquidity = min(liquidity0, liquidity1) if liquidity0 > 0 and liquidity1 > 0 else max(liquidity0, liquidity1)
        else:
            # Price above range - all liquidity in token1
            if sqrt_price_b_x96 == sqrt_price_a_x96:
                return 0
            liquidity = (amount1 * UniswapV3Math.Q96) // (sqrt_price_b_x96 - sqrt_price_a_x96)

        return max(0, liquidity)

    @staticmethod
    def get_amounts_for_liquidity(
        sqrt_price_x96: int,
        sqrt_price_a_x96: int,
        sqrt_price_b_x96: int,
        liquidity: int
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
            amount0 = (liquidity * (sqrt_price_b_x96 - sqrt_price_a_x96) * UniswapV3Math.Q96) // (
                sqrt_price_a_x96 * sqrt_price_b_x96
            )
            amount1 = 0
        elif sqrt_price_x96 < sqrt_price_b_x96:
            # Price in range
            if sqrt_price_x96 == 0 or sqrt_price_b_x96 == 0:
                return (0, 0)
            amount0 = (liquidity * (sqrt_price_b_x96 - sqrt_price_x96) * UniswapV3Math.Q96) // (
                sqrt_price_x96 * sqrt_price_b_x96
            )
            amount1 = (liquidity * (sqrt_price_x96 - sqrt_price_a_x96)) // UniswapV3Math.Q96
        else:
            # Price above range - all in token1
            amount0 = 0
            amount1 = (liquidity * (sqrt_price_b_x96 - sqrt_price_a_x96)) // UniswapV3Math.Q96

        return (max(0, amount0), max(0, amount1))


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
        fee_rate: float = DEFAULT_FEE_RATE,
        default_pool_liquidity: int = 10_000_000_000_000_000_000,  # 10 ETH worth
        validator: Optional['SN98Validator'] = None,
        rebalance_check_interval: int = 1000  # Check every N blocks
    ):
        """
        Initialize backtester.

        Args:
            data_source: Data source for historical data (implements DataSource interface)
            fee_rate: Pool fee rate (e.g., 0.003 for 0.3%)
            default_pool_liquidity: Default total pool liquidity if not available from DB
            validator: Optional validator instance for querying miners via dendrite
            rebalance_check_interval: How often to check miner for rebalance (in blocks)
        """
        self.db = data_source  # Keep as self.db for compatibility
        self.math = UniswapV3Math()
        self.fee_rate = fee_rate
        self.default_pool_liquidity = default_pool_liquidity
        self.validator = validator
        self.rebalance_check_interval = rebalance_check_interval

    def calculate_hodl_baseline(
        self,
        pair_address: str,
        initial_amount0: int,
        initial_amount1: int,
        start_block: int,
        end_block: int
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
        start_price = self.db.get_price_at_block(pair_address, start_block)
        end_price = self.db.get_price_at_block(pair_address, end_block)

        if start_price is None or end_price is None:
            logger.warning("Could not fetch prices for HODL baseline, using fallback")
            # Fallback: assume no price change
            start_price = start_price or 1.0
            end_price = end_price or start_price

        # Final value in token1 terms (tokens unchanged, just price differs)
        final_value = initial_amount0 * end_price + initial_amount1
        return final_value

    def _calculate_position_liquidity_and_amounts(
        self,
        position: Position,
        current_price: float
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
        price_lower = self.math.tick_to_price(position.tick_lower)
        price_upper = self.math.tick_to_price(position.tick_upper)

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

    def _calculate_liquidity_share(
        self,
        position_liquidity: float,
        event: Dict[str, Any],
        event_price: float,
        price_lower: float,
        price_upper: float
    ) -> float:
        """
        Calculate the share of fees this position earns from a swap.

        This is the key improvement: instead of assuming 1% share,
        we calculate the actual share based on:
        1. Position liquidity
        2. Total pool liquidity (from swap event)
        3. Whether price is in range

        Args:
            position_liquidity: Liquidity of the position
            event: Swap event data
            event_price: Price at time of swap
            price_lower: Position lower price bound
            price_upper: Position upper price bound

        Returns:
            Liquidity share (0.0 to 1.0)
        """
        # Check if position is in range
        if event_price < price_lower or event_price > price_upper:
            return 0.0

        # Get total pool liquidity from event (if available)
        pool_liquidity = event.get('liquidity')
        if pool_liquidity:
            pool_liquidity = float(pool_liquidity)
        else:
            # Fallback: estimate from default
            pool_liquidity = float(self.default_pool_liquidity)

        if pool_liquidity <= 0:
            logger.warning(
                f"Pool liquidity is <= 0 ({pool_liquidity}). "
                "This suggests bad data or a bug. Returning 0 share."
            )
            return 0.0

        # Calculate share (capped at 100% to handle edge cases)
        share = min(1.0, position_liquidity / pool_liquidity)
        return share

    def _simulate_rebalances(
        self,
        swap_events: List[Dict[str, Any]],
        rebalance_rule: Optional[RebalanceRule],
        positions: List[Position],
        start_block: int,
        pair_address: str = "",
        round_id: str = "",
        miner_uid: Optional[int] = None
    ) -> Tuple[int, List[int]]:
        """
        Simulate when rebalances would occur based on strategy rules.

        Supports two modes:
        1. Static rules (rebalance_rule): Simple trigger-based logic
        2. Dynamic (miner_endpoint): Call miner to make decisions

        Args:
            swap_events: Historical swap events
            rebalance_rule: Strategy's rebalance rule
            positions: Current positions
            start_block: Starting block
            pair_address: Pool address (for miner calls)
            round_id: Round ID (for miner calls)

        Returns:
            Tuple of (num_rebalances, list of rebalance blocks)
        """
        if not swap_events:
            return 0, []

        # If validator and miner_uid are configured, use dynamic rebalancing
        if self.validator and miner_uid is not None:
            return self._simulate_rebalances_dynamic(
                swap_events=swap_events,
                miner_uid=miner_uid,
                positions=positions,
                start_block=start_block,
                pair_address=pair_address,
                round_id=round_id
            )

        # Otherwise use static rule-based rebalancing
        if not rebalance_rule:
            return 0, []

        rebalance_blocks = []
        last_rebalance_block = start_block
        cooldown_blocks = rebalance_rule.cooldown_blocks

        # Get position tick bounds
        if not positions:
            return 0, []

        # Use first position's bounds as trigger reference
        tick_lower = positions[0].tick_lower
        tick_upper = positions[0].tick_upper

        for event in swap_events:
            event_block = event.get('block_number', 0)
            if not event_block:
                continue

            # Check cooldown
            if event_block - last_rebalance_block < cooldown_blocks:
                continue

            # Get current tick from event
            event_tick = event.get('tick')
            if event_tick is None:
                # Estimate from sqrt_price if tick not available
                sqrt_price_x96 = event.get('sqrt_price_x96')
                if sqrt_price_x96:
                    event_tick = self.math.get_tick_at_sqrt_ratio(int(sqrt_price_x96))
                else:
                    continue

            event_tick = int(event_tick)

            # Check if price is outside range (trigger condition)
            if rebalance_rule.trigger == "price_outside_range":
                if event_tick < tick_lower or event_tick > tick_upper:
                    rebalance_blocks.append(event_block)
                    last_rebalance_block = event_block

        return len(rebalance_blocks), rebalance_blocks

    def _simulate_rebalances_dynamic(
        self,
        swap_events: List[Dict[str, Any]],
        positions: List[Position],
        start_block: int,
        pair_address: str,
        round_id: str,
        miner_uid: int
    ) -> Tuple[int, List[int]]:
        """
        Simulate rebalances by querying miner via dendrite at regular intervals.

        This is the Option 2 architecture: validators ask miners for
        rebalance decisions during backtesting using Bittensor's dendrite/axon.

        Args:
            swap_events: Historical swap events
            positions: Current positions
            start_block: Starting block
            pair_address: Pool address
            round_id: Round identifier
            miner_uid: Miner UID to query

        Returns:
            Tuple of (num_rebalances, list of rebalance blocks)
        """
        if not self.validator or not positions:
            return 0, []

        rebalance_blocks = []
        current_positions = positions
        last_check_block = start_block

        # Group events by block intervals
        block_to_price = {}
        for event in swap_events:
            block = event.get('block_number', 0)
            sqrt_price_x96 = event.get('sqrt_price_x96')
            if block and sqrt_price_x96:
                price = (int(sqrt_price_x96) / (2 ** 96)) ** 2
                block_to_price[block] = price

        if not block_to_price:
            return 0, []

        # Check at regular intervals
        sorted_blocks = sorted(block_to_price.keys())

        for block in sorted_blocks:
            # Only check at intervals
            if block - last_check_block < self.rebalance_check_interval:
                continue

            price = block_to_price[block]
            last_check_block = block

            # Query miner using validator's dendrite
            result = self.validator.query_miner_rebalance(
                miner_uid=miner_uid,
                block_number=block,
                current_price=price,
                current_positions=current_positions,
                pair_address=pair_address,
                chain_id=8453,
                round_id=round_id
            )

            if result:
                should_rebalance, new_positions, reason = result

                if should_rebalance:
                    rebalance_blocks.append(block)
                    # Update positions for next iteration
                    if new_positions:
                        current_positions = new_positions
                    logger.debug(
                        f"Rebalance at block {block}: {reason}"
                    )
            else:
                logger.warning(f"Failed to query miner {miner_uid} for rebalance at block {block}")
                # Continue with static simulation if miner is unreachable
                continue

        return len(rebalance_blocks), rebalance_blocks

    def simulate_position(
        self,
        pair_address: str,
        position: Position,
        start_block: int,
        end_block: int,
        current_price: float,
        fee_rate: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Simulate a single LP position over a block range using V3 concentrated liquidity math.

        Args:
            pair_address: Pool address
            position: LP position to simulate
            start_block: Starting block
            end_block: Ending block
            current_price: Price at start
            fee_rate: Optional override for pool fee rate

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
        fee_rate = fee_rate or self.fee_rate

        # Convert tick bounds to prices
        price_lower = self.math.tick_to_price(position.tick_lower)
        price_upper = self.math.tick_to_price(position.tick_upper)

        # Get swap events in this range
        swap_events = self.db.get_swap_events(pair_address, start_block, end_block)

        # Initial amounts from allocation
        initial_amount0 = int(position.allocation0)
        initial_amount1 = int(position.allocation1)

        # Calculate position liquidity AND actual amounts deployed
        # In V3, you can't always deploy all tokens - only what fits the limiting token
        position_liquidity, actual_amount0, actual_amount1 = self._calculate_position_liquidity_and_amounts(
            position, current_price
        )

        # Track excess tokens that couldn't be deployed (they're just held)
        excess_amount0 = max(0.0, initial_amount0 - actual_amount0)
        excess_amount1 = max(0.0, initial_amount1 - actual_amount1)

        logger.debug(
            f"Position deployment: allocated=({initial_amount0}, {initial_amount1}), "
            f"actual=({actual_amount0:.2f}, {actual_amount1:.2f}), "
            f"excess=({excess_amount0:.2f}, {excess_amount1:.2f}), liquidity={position_liquidity:.2f}"
        )

        # Track fees
        total_fees0 = 0.0
        total_fees1 = 0.0
        in_range_count = 0
        total_swaps = len(swap_events)

        # Simulate each swap for fee accumulation
        for event in swap_events:
            # Calculate price from sqrt_price_x96 if available
            sqrt_price_x96 = event.get('sqrt_price_x96')
            if sqrt_price_x96:
                sqrt_price = int(sqrt_price_x96)
                event_price = (sqrt_price / (2 ** 96)) ** 2
            else:
                event_price = current_price

            # Check if position is in range
            if price_lower <= event_price <= price_upper:
                in_range_count += 1

                # Get swap amounts (signed: positive = token came IN, negative = token went OUT)
                # In Uniswap V3, fees are ONLY charged on the INPUT token
                raw_amount0 = float(event.get('amount0', 0) or 0)
                raw_amount1 = float(event.get('amount1', 0) or 0)

                # Calculate liquidity share for this swap
                liquidity_share = self._calculate_liquidity_share(
                    position_liquidity,
                    event,
                    event_price,
                    price_lower,
                    price_upper
                )

                # Fees earned ONLY on the input token (the one with positive amount)
                # If amount0 > 0: user swapped token0 for token1, fee is on token0
                # If amount1 > 0: user swapped token1 for token0, fee is on token1
                if raw_amount0 > 0:
                    total_fees0 += raw_amount0 * fee_rate * liquidity_share
                elif raw_amount1 > 0:
                    total_fees1 += raw_amount1 * fee_rate * liquidity_share

        # Get final price
        final_price = self.db.get_price_at_block(pair_address, end_block) or current_price

        # Calculate final amounts using V3 concentrated liquidity math
        sqrt_price_lower = math.sqrt(price_lower)
        sqrt_price_upper = math.sqrt(price_upper)
        sqrt_price_final = math.sqrt(final_price)

        if final_price <= price_lower:
            # Price below range - all in token0
            if sqrt_price_lower > 0 and sqrt_price_upper > 0:
                final_amount0 = position_liquidity * (sqrt_price_upper - sqrt_price_lower) / (
                    sqrt_price_lower * sqrt_price_upper
                )
            else:
                final_amount0 = actual_amount0
            final_amount1 = 0
        elif final_price >= price_upper:
            # Price above range - all in token1
            final_amount0 = 0
            final_amount1 = position_liquidity * (sqrt_price_upper - sqrt_price_lower)
        else:
            # Price in range
            if sqrt_price_final > 0 and sqrt_price_upper > 0:
                final_amount0 = position_liquidity * (sqrt_price_upper - sqrt_price_final) / (
                    sqrt_price_final * sqrt_price_upper
                )
            else:
                final_amount0 = 0
            final_amount1 = position_liquidity * (sqrt_price_final - sqrt_price_lower)

        # Add back the excess tokens that weren't deployed (they're just held, unchanged)
        final_amount0 += excess_amount0
        final_amount1 += excess_amount1

        # Calculate IL: compare LP value vs HODL value
        # IMPORTANT: Use ACTUAL deployed amounts for HODL baseline, not allocations!
        # The excess tokens are held and don't experience IL
        hodl_value_deployed = actual_amount0 * final_price + actual_amount1
        lp_value_deployed = (final_amount0 - excess_amount0) * final_price + (final_amount1 - excess_amount1)

        # IL is only on the deployed portion
        if hodl_value_deployed > 0:
            impermanent_loss = max(0.0, (hodl_value_deployed - lp_value_deployed) / hodl_value_deployed)
        else:
            impermanent_loss = 0.0

        # Calculate in-range ratio
        in_range_ratio = in_range_count / total_swaps if total_swaps > 0 else 0.0

        return {
            'fees_collected': total_fees0 * final_price + total_fees1,
            'final_amount0': final_amount0,
            'final_amount1': final_amount1,
            'impermanent_loss': impermanent_loss,
            'fees0': total_fees0,
            'fees1': total_fees1,
            'in_range_ratio': in_range_ratio,
            'position_liquidity': position_liquidity,
            'actual_amount0': actual_amount0,
            'actual_amount1': actual_amount1,
            'excess_amount0': excess_amount0,
            'excess_amount1': excess_amount1
        }

    def backtest_strategy(
        self,
        pair_address: str,
        strategy: Strategy,
        initial_amount0: int,
        initial_amount1: int,
        start_block: int,
        end_block: int,
        fee_rate: Optional[float] = None,
        miner_uid: Optional[int] = None,
        round_id: str = ""
    ) -> PerformanceMetrics:
        """
        Backtest a complete strategy with multiple positions.

        Args:
            pair_address: Pool address
            strategy: Strategy to test
            initial_amount0: Initial amount of token0
            initial_amount1: Initial amount of token1
            start_block: Starting block
            end_block: Ending block
            fee_rate: Optional pool fee rate override
            miner_endpoint: Optional miner URL for dynamic rebalance decisions
            round_id: Round identifier for rebalance requests

        Returns:
            PerformanceMetrics object
        """
        fee_rate = fee_rate or self.fee_rate

        # Get current price
        current_price = self.db.get_price_at_block(pair_address, start_block)
        if current_price is None:
            logger.warning("Could not fetch start price, using fallback of 1.0")
            current_price = 1.0

        final_price = self.db.get_price_at_block(pair_address, end_block)
        if final_price is None:
            logger.warning("Could not fetch end price, using start price")
            final_price = current_price

        # Calculate HODL baseline
        hodl_final_value = self.calculate_hodl_baseline(
            pair_address, initial_amount0, initial_amount1, start_block, end_block
        )
        initial_value = initial_amount0 * current_price + initial_amount1
        hodl_pnl = hodl_final_value - initial_value

        # Simulate each position
        total_fees = 0.0
        weighted_il = 0.0
        total_deployed_value = 0.0
        final_value = 0.0

        for position in strategy.positions:
            result = self.simulate_position(
                pair_address, position, start_block, end_block, current_price, fee_rate
            )

            total_fees += result['fees_collected']

            # Weight IL by ACTUAL DEPLOYED value (not allocation)
            # This is important because excess tokens don't experience IL
            actual_amount0 = result.get('actual_amount0', int(position.allocation0))
            actual_amount1 = result.get('actual_amount1', int(position.allocation1))
            deployed_value = actual_amount0 * current_price + actual_amount1
            weighted_il += result['impermanent_loss'] * deployed_value
            total_deployed_value += deployed_value

            final_value += result['final_amount0'] * final_price + result['final_amount1']

        # Calculate average IL weighted by deployed position size
        avg_il = weighted_il / total_deployed_value if total_deployed_value > 0 else 0.0

        # Calculate PnL
        net_pnl = final_value + total_fees - initial_value
        net_pnl_vs_hodl = net_pnl - hodl_pnl

        # Simulate rebalances
        swap_events = self.db.get_swap_events(pair_address, start_block, end_block)

        num_rebalances, _ = self._simulate_rebalances(
            swap_events,
            strategy.rebalance_rule,
            strategy.positions,
            start_block,
            pair_address=pair_address,
            round_id=round_id,
            miner_uid=miner_uid
        )

        logger.info(
            f"Backtest results: PnL={net_pnl:.2f}, vs HODL={net_pnl_vs_hodl:.2f}, "
            f"Fees={total_fees:.2f}, IL={avg_il:.2%}, Rebalances={num_rebalances}"
        )

        return PerformanceMetrics(
            net_pnl=net_pnl,
            hodl_pnl=hodl_pnl,
            net_pnl_vs_hodl=net_pnl_vs_hodl,
            total_fees_collected=total_fees,
            impermanent_loss=avg_il,
            num_rebalances=num_rebalances
        )

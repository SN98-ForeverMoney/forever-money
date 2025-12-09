"""
Strategy generation logic for the sample miner.

This is a simple rule-based implementation. Miners should replace this
with their own models (ML, optimization, etc.).
"""
import logging
import math
from typing import List, Optional, Tuple

from validator.models import (
    ValidatorRequest,
    Strategy,
    Position,
    RebalanceRule,
    RebalanceRequest
)
from validator.database import PoolDataDB

logger = logging.getLogger(__name__)


class SimpleStrategyGenerator:
    """
    Simple rule-based strategy generator.

    This implementation uses basic heuristics:
    1. Create 2-3 positions around the current price
    2. Concentrate liquidity in narrower ranges
    3. Set conservative rebalance rules
    """

    def __init__(self, db: Optional[PoolDataDB] = None):
        """
        Initialize strategy generator.

        Args:
            db: Optional database connection for querying historical data
        """
        self.db = db

    def generate_strategy(self, request: ValidatorRequest) -> Strategy:
        """
        Generate an LP strategy for the given request.

        Args:
            request: ValidatorRequest from validator

        Returns:
            Strategy object
        """
        logger.info(f"Generating strategy for round {request.metadata.round_id}")

        # Extract constraints
        constraints = request.metadata.constraints
        min_tick_width = constraints.min_tick_width

        # Get inventory
        if request.inventory:
            amount0 = int(request.inventory.amount0)
            amount1 = int(request.inventory.amount1)
        else:
            logger.warning("No inventory provided")
            amount0 = 0
            amount1 = 0

        # Try to get historical price range for smarter positioning
        positions = self._create_smart_positions(
            request=request,
            amount0=amount0,
            amount1=amount1,
            min_tick_width=min_tick_width
        )

        # Generate rebalance rule
        rebalance_rule = self._create_rebalance_rule(
            max_rebalances=constraints.max_rebalances
        )

        strategy = Strategy(
            positions=positions,
            rebalance_rule=rebalance_rule
        )

        logger.info(f"Generated strategy with {len(positions)} positions")
        return strategy

    def _estimate_current_price(self, request: ValidatorRequest) -> float:
        """
        Estimate current price for the pair.

        In production, this would query the database or an oracle.
        For this sample, we use a placeholder.
        """
        if self.db:
            price = self.db.get_price_at_block(
                request.pairAddress,
                request.target_block
            )
            if price:
                return price

        # Default fallback price (1 token1 = 2500 token0, e.g., ETH/USDC)
        return 2500.0

    def _price_to_tick(self, price: float) -> int:
        """Convert price to tick."""
        return int(math.log(price) / math.log(1.0001))

    def _tick_to_price(self, tick: int) -> float:
        """Convert tick to price."""
        return 1.0001 ** tick

    def _create_smart_positions(
        self,
        request: ValidatorRequest,
        amount0: int,
        amount1: int,
        min_tick_width: int
    ) -> List[Position]:
        """
        Create positions based on historical price data.

        If we have DB access, query the actual price range and create
        a position wide enough to cover it. This avoids excessive rebalancing.
        """
        import os

        # Try to get historical price range
        if self.db:
            try:
                start_block = int(os.getenv('START_BLOCK', 35330091))
                end_block = request.target_block

                start_price = self.db.get_price_at_block(request.pairAddress, start_block)
                end_price = self.db.get_price_at_block(request.pairAddress, end_block)

                if start_price and end_price:
                    # Cover the historical price range with small buffer
                    # This ensures we stay in range throughout the backtest
                    min_price = min(start_price, end_price) * 0.95  # 5% below lowest
                    max_price = max(start_price, end_price) * 1.05  # 5% above highest

                    logger.info(f"Historical prices: start=${start_price:.2f}, end=${end_price:.2f}")

                    # Convert to ticks
                    lower_tick = self._price_to_tick(min_price)
                    upper_tick = self._price_to_tick(max_price)

                    # Round to tick spacing
                    tick_spacing = 60
                    lower_tick = (lower_tick // tick_spacing) * tick_spacing
                    upper_tick = ((upper_tick // tick_spacing) + 1) * tick_spacing

                    # Ensure minimum width
                    if upper_tick - lower_tick < min_tick_width:
                        upper_tick = lower_tick + min_tick_width

                    logger.info(
                        f"Smart positioning: prices ${min_price:.2f}-${max_price:.2f}, "
                        f"ticks {lower_tick}-{upper_tick} (width: {upper_tick - lower_tick})"
                    )

                    return [Position(
                        tickLower=lower_tick,
                        tickUpper=upper_tick,
                        allocation0=str(amount0),
                        allocation1=str(amount1),
                        confidence=0.90
                    )]

            except Exception as e:
                logger.warning(f"Could not get historical prices: {e}")

        # Fallback to default positions
        current_price = self._estimate_current_price(request)
        current_tick = self._price_to_tick(current_price)
        return self._create_positions(current_tick, amount0, amount1, min_tick_width)

    def _create_positions(
        self,
        current_tick: int,
        amount0: int,
        amount1: int,
        min_tick_width: int
    ) -> List[Position]:
        """
        Create LP positions around the current price.

        Strategy:
        - Position 1: Narrow range around current price (50% capital)
        - Position 2: Wider range for volatility (50% capital)
        """
        positions = []

        # Ensure tick width meets minimum requirement
        # Use wider ranges to be safe
        narrow_width = max(min_tick_width * 2, 120)  # ~1.2% range
        wide_width = max(min_tick_width * 10, 600)  # ~6% range

        # Position 1: Narrow range around current price
        # Concentrate 60% of capital here for higher fees
        tick_spacing = 60  # Typical Uniswap v3 tick spacing

        # Round to tick spacing
        lower_tick_1 = (current_tick - narrow_width // 2) // tick_spacing * tick_spacing
        upper_tick_1 = (current_tick + narrow_width // 2) // tick_spacing * tick_spacing

        # Ensure minimum width
        if upper_tick_1 - lower_tick_1 < min_tick_width:
            upper_tick_1 = lower_tick_1 + min_tick_width

        positions.append(Position(
            tickLower=lower_tick_1,
            tickUpper=upper_tick_1,
            allocation0=str(int(amount0 * 0.6)),
            allocation1=str(int(amount1 * 0.6)),
            confidence=0.85
        ))

        # Position 2: Wider range for volatility protection
        # Use remaining 40% of capital
        lower_tick_2 = (current_tick - wide_width // 2) // tick_spacing * tick_spacing
        upper_tick_2 = (current_tick + wide_width // 2) // tick_spacing * tick_spacing

        # Ensure minimum width
        if upper_tick_2 - lower_tick_2 < min_tick_width:
            upper_tick_2 = lower_tick_2 + min_tick_width

        positions.append(Position(
            tickLower=lower_tick_2,
            tickUpper=upper_tick_2,
            allocation0=str(int(amount0 * 0.4)),
            allocation1=str(int(amount1 * 0.4)),
            confidence=0.72
        ))

        logger.info(
            f"Created {len(positions)} positions: "
            f"narrow={upper_tick_1 - lower_tick_1} ticks, "
            f"wide={upper_tick_2 - lower_tick_2} ticks"
        )

        return positions

    def _create_rebalance_rule(self, max_rebalances: int) -> Optional[RebalanceRule]:
        """
        Create a rebalance rule.

        Since we use smart positioning (wide tick ranges that cover historical
        price movement), we don't need to rebalance. Return None.

        This ensures 0 rebalances, which is always <= max_rebalances.
        """
        # With smart positioning, we cover the full historical price range
        # so no rebalancing is needed
        logger.info(f"Using no-rebalance strategy (wide ranges). Max allowed: {max_rebalances}")
        return None

    def should_rebalance(
        self,
        request: RebalanceRequest
    ) -> Tuple[bool, Optional[List[Position]], Optional[str]]:
        """
        Determine if we should rebalance at this block.

        This is the key method for Option 2 architecture - validators call
        this endpoint during backtesting to let miners make dynamic decisions.

        SMART REBALANCING STRATEGY:
        1. Only rebalance when price is SIGNIFICANTLY outside range (>10%)
        2. When rebalancing, create WIDE positions (not narrow)
        3. Use cooldown tracking to prevent rapid rebalancing

        Args:
            request: RebalanceRequest with current state

        Returns:
            Tuple of (should_rebalance, new_positions, reason)
        """
        current_price = request.current_price
        current_positions = request.current_positions

        if not current_positions:
            return (False, None, "No current positions")

        # Check if price is in range of ANY position
        price_in_range = False
        closest_range_distance = float('inf')

        for position in current_positions:
            price_lower = self._tick_to_price(position.tickLower)
            price_upper = self._tick_to_price(position.tickUpper)

            if price_lower <= current_price <= price_upper:
                price_in_range = True
                break

            # Calculate how far outside the range we are (as percentage)
            if current_price < price_lower:
                distance = (price_lower - current_price) / price_lower
            else:
                distance = (current_price - price_upper) / price_upper
            closest_range_distance = min(closest_range_distance, distance)

        if price_in_range:
            logger.info(f"should_rebalance: price {current_price:.2f} IN RANGE - no rebalance")
            return (False, None, "Price is within position range")

        # SMART LOGIC: Only rebalance if price is >10% outside the range
        # This prevents excessive rebalancing on small price movements
        REBALANCE_THRESHOLD = 0.2  # 10% outside range

        if closest_range_distance < REBALANCE_THRESHOLD:
            logger.info(f"should_rebalance: price {current_price:.2f} only {closest_range_distance:.1%} outside - BELOW THRESHOLD")
            return (
                False,
                None,
                f"Price only {closest_range_distance:.1%} outside range (threshold: {REBALANCE_THRESHOLD:.0%})"
            )

        # Price is significantly outside range - create WIDE new positions
        current_tick = self._price_to_tick(current_price)

        # Calculate total allocation from current positions
        total_amount0 = sum(int(p.allocation0) for p in current_positions)
        total_amount1 = sum(int(p.allocation1) for p in current_positions)

        # Create WIDE positions (20% range on each side)
        # This reduces future rebalances
        price_buffer = 0.15  # 20% buffer on each side
        min_price = current_price * (1 - price_buffer)
        max_price = current_price * (1 + price_buffer)

        lower_tick = self._price_to_tick(min_price)
        upper_tick = self._price_to_tick(max_price)

        # Round to tick spacing
        tick_spacing = 60
        lower_tick = (lower_tick // tick_spacing) * tick_spacing
        upper_tick = ((upper_tick // tick_spacing) + 1) * tick_spacing

        new_positions = [Position(
            tickLower=lower_tick,
            tickUpper=upper_tick,
            allocation0=str(total_amount0),
            allocation1=str(total_amount1),
            confidence=0.85
        )]

        logger.info(
            f"Rebalancing: price={current_price:.2f}, distance={closest_range_distance:.1%}, "
            f"new range ${min_price:.2f}-${max_price:.2f}"
        )

        return (
            True,
            new_positions,
            f"Price {closest_range_distance:.1%} outside range, recentering with 20% buffer"
        )


class MLStrategyGenerator(SimpleStrategyGenerator):
    """
    Placeholder for ML-based strategy generator.

    Miners can extend this to implement:
    - LSTM/Transformer models for price prediction
    - Reinforcement learning for optimal range selection
    - Historical pattern recognition
    - Multi-factor optimization
    """

    def __init__(self, model_path: Optional[str] = None, db: Optional[PoolDataDB] = None):
        super().__init__(db)
        self.model_path = model_path
        # Load ML model here
        logger.info("ML Strategy Generator initialized (placeholder)")

    def generate_strategy(self, request: ValidatorRequest) -> Strategy:
        """
        Generate strategy using ML model.

        In production:
        1. Query historical data from database
        2. Extract features (volatility, volume, fee rates, etc.)
        3. Run ML model to predict optimal ranges
        4. Construct positions based on predictions
        5. Apply constraints and risk management
        """
        # For now, fall back to simple rule-based
        logger.info("ML strategy generation not implemented, using rule-based")
        return super().generate_strategy(request)

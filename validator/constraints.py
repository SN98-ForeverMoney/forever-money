"""
Constraint validation for miner strategies.
"""
import logging
from typing import List, Tuple
from protocol import Strategy, Position
from validator.models import Constraints

logger = logging.getLogger(__name__)


class ConstraintValidator:
    """
    Validates that miner strategies comply with round constraints.
    Non-compliant strategies receive a score of 0.
    """

    def __init__(self, constraints: Constraints):
        self.constraints = constraints

    def validate_strategy(self, strategy: Strategy) -> Tuple[bool, List[str]]:
        """
        Validate a strategy against all constraints.

        Args:
            strategy: The strategy to validate

        Returns:
            Tuple of (is_valid, list_of_violations)
        """
        violations = []

        # Validate tick widths
        tick_violations = self._validate_tick_widths(strategy.positions)
        violations.extend(tick_violations)

        # Validate rebalance limits
        rebalance_violations = self._validate_rebalances(strategy)
        violations.extend(rebalance_violations)

        # Note: IL validation happens during backtesting
        # We add a placeholder check here
        il_violations = self._validate_impermanent_loss(strategy)
        violations.extend(il_violations)

        # Validate position allocations
        allocation_violations = self._validate_allocations(strategy.positions)
        violations.extend(allocation_violations)

        is_valid = len(violations) == 0
        return is_valid, violations

    def _validate_tick_widths(self, positions: List[Position]) -> List[str]:
        """
        Ensure all positions meet minimum tick width requirement.
        """
        violations = []
        min_tick_width = self.constraints.min_tick_width

        for i, position in enumerate(positions):
            tick_width = position.tick_upper - position.tick_lower

            if tick_width < min_tick_width:
                violation = (
                    f"Position {i}: tick width {tick_width} is less than "
                    f"minimum required {min_tick_width}"
                )
                violations.append(violation)
                logger.warning(violation)

        return violations

    def _validate_rebalances(self, strategy: Strategy) -> List[str]:
        """
        Validate rebalance rules against max_rebalances constraint.
        """
        violations = []
        max_rebalances = self.constraints.max_rebalances

        if strategy.rebalance_rule:
            # Check cooldown blocks is reasonable
            if strategy.rebalance_rule.cooldown_blocks < 100:
                violation = (
                    f"Rebalance cooldown {strategy.rebalance_rule.cooldown_blocks} "
                    f"blocks is too aggressive (minimum 100 blocks recommended)"
                )
                violations.append(violation)
                logger.warning(violation)

            # Note: Actual rebalance count is determined during backtesting
            # This is a pre-check for obvious violations

        return violations

    def _validate_impermanent_loss(self, strategy: Strategy) -> List[str]:
        """
        Placeholder for IL validation.
        Actual IL is calculated during backtesting.
        """
        violations = []

        # Check if positions are reasonable (not too wide or too narrow)
        for i, position in enumerate(strategy.positions):
            tick_width = position.tick_upper - position.tick_lower

            # Extremely wide positions (> 10000 ticks) may have high IL
            if tick_width > 10000:
                logger.info(
                    f"Position {i}: very wide range ({tick_width} ticks), "
                    f"may incur high impermanent loss"
                )

        return violations

    def _validate_allocations(self, positions: List[Position]) -> List[str]:
        """
        Validate that allocations are positive and reasonable.
        """
        violations = []

        for i, position in enumerate(positions):
            amount0 = int(position.allocation0)
            amount1 = int(position.allocation1)

            # At least one allocation must be non-zero
            if amount0 == 0 and amount1 == 0:
                violation = f"Position {i}: both allocations are zero"
                violations.append(violation)
                logger.warning(violation)

            # Check for negative allocations (shouldn't be possible with string->int)
            if amount0 < 0 or amount1 < 0:
                violation = f"Position {i}: negative allocation detected"
                violations.append(violation)
                logger.error(violation)

        return violations

    def validate_performance_metrics(
        self,
        impermanent_loss: float,
        num_rebalances: int
    ) -> Tuple[bool, List[str]]:
        """
        Validate performance metrics after backtesting.

        Args:
            impermanent_loss: IL as a fraction (e.g., 0.12 = 12%)
            num_rebalances: Number of rebalances executed

        Returns:
            Tuple of (is_valid, list_of_violations)
        """
        violations = []

        # Check IL constraint
        if impermanent_loss > self.constraints.max_il:
            violation = (
                f"Impermanent loss {impermanent_loss:.2%} exceeds maximum "
                f"allowed {self.constraints.max_il:.2%}"
            )
            violations.append(violation)
            logger.warning(violation)

        # Check rebalance constraint
        if num_rebalances > self.constraints.max_rebalances:
            violation = (
                f"Number of rebalances {num_rebalances} exceeds maximum "
                f"allowed {self.constraints.max_rebalances}"
            )
            violations.append(violation)
            logger.warning(violation)

        is_valid = len(violations) == 0
        return is_valid, violations

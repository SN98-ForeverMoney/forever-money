"""
Multi-Position Miner (MinimalMiner)

This module provides a volatility-aware, multi-position liquidity provision (LP) strategy
for testing in the minarch_lab framework.

Key Features:
- Maintains two LP positions simultaneously:
    1. Wide range (70% of inventory) -> lower risk, wider coverage
    2. Tight range (30% of inventory) -> higher fee potential, more concentrated
- Volatility-driven dynamic positioning
- Automatic rebalance when price drifts outside buffer zones
- Supports recent market price history for volatility calculation
"""

import logging
import math
from dataclasses import dataclass
from typing import List

# Configure logging
logging.basicConfig(
    filename="output.log",
    filemode="w",
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class Position:
    tick_lower: float
    tick_upper: float
    allocation0: float
    allocation1: float


@dataclass
class Inventory:
    amount0: float
    amount1: float


class MinimalMiner:
    """
    Volatility-aware multi-position miner.

    Maintains two positions (wide and tight) and rebalances automatically
    based on market volatility and current tick location relative to position bounds.

    Attributes:
        inventory (Inventory): Current inventory for LP positions
        positions (List[Position]): List of active positions (wide, tight)
        width_factor (float): Multiplier for volatility to calculate wide range width
        volatility_window (int): Number of recent price points used for volatility calculation
    """

    def __init__(
        self,
        inventory: Inventory,
        width_factor: float = 3.0,
        volatility_window: int = 10,
    ):
        """
        Initialize the miner with inventory and configuration.

        Args:
            inventory (Inventory): Initial token balances
            width_factor (float, optional): Multiplier for wide position width based on volatility
            volatility_window (int, optional): Number of recent prices to compute volatility
        """
        self.inventory = inventory
        self.positions: List[Position] = []
        self.width_factor = width_factor
        self.volatility_window = volatility_window
        logger.info(
            f"Starting Miner V1 with inventory: {inventory}, width factor: {width_factor} and volatility window: {volatility_window}"
        )

    def compute_volatility(self, recent_prices: List[float]) -> float:
        """
        Compute price volatility from recent prices.

        Args:
            recent_prices (List[float]): List of recent price ticks

        Returns:
            float: Standard deviation of relative price changes
        """
        if len(recent_prices) < self.volatility_window:
            return 0.0

        price_changes = [
            (recent_prices[i] - recent_prices[i - 1]) / recent_prices[i - 1]
            for i in range(1, len(recent_prices))
        ]
        mean = sum(price_changes) / len(price_changes)
        variance = sum((x - mean) ** 2 for x in price_changes) / len(price_changes)
        return math.sqrt(variance)

    def rebalance_query_handler(
        self, current_tick: int, tick_spacing: int, recent_prices: List[float]
    ):
        """
        Determine whether to rebalance and generate new LP positions.

        Logic:
        - Rebalance if no positions exist or price is within 20% of wide position edge
        - Wide range uses 70% of inventory
        - Tight range uses 30% of inventory
        - Position widths are volatility-scaled

        Args:
            current_tick (int): Current market tick
            tick_spacing (int): Minimum tick spacing for the pool
            recent_prices (List[float]): List of recent ticks for volatility calculation

        Returns:
            List[Position]: Updated list of LP positions
        """
        should_rebalance = False

        if not self.positions:
            should_rebalance = True
        else:
            # check wide position
            pos = self.positions[0]
            buffer = 0.2 * (pos.tick_upper - pos.tick_lower)
            if (
                current_tick < pos.tick_lower + buffer
                or current_tick > pos.tick_upper - buffer
            ):
                should_rebalance = True

        if should_rebalance:
            volatility = self.compute_volatility(recent_prices)
            logger.info(
                f"Calculated volatility factor as {volatility} using recent prices: {recent_prices}"
            )

            width = volatility * self.width_factor
            tight_width = width * 0.3

            center_tick = (current_tick // tick_spacing) * tick_spacing

            # Wide range (70% allocation)
            wide_lower = int((center_tick - width) // tick_spacing * tick_spacing)
            wide_upper = int((center_tick + width) // tick_spacing * tick_spacing)

            wide_pos = Position(
                tick_lower=wide_lower,
                tick_upper=wide_upper,
                allocation0=float(self.inventory.amount0) * 0.7,
                allocation1=float(self.inventory.amount1) * 0.7,
            )

            # Tight range (30% allocation)
            tight_lower = int(
                (center_tick - tight_width) // tick_spacing * tick_spacing
            )
            tight_upper = int(
                (center_tick + tight_width) // tick_spacing * tick_spacing
            )

            tight_pos = Position(
                tick_lower=tight_lower,
                tick_upper=tight_upper,
                allocation0=float(self.inventory.amount0) * 0.3,
                allocation1=float(self.inventory.amount1) * 0.3,
            )

            self.positions = [wide_pos, tight_pos]

            logger.info(
                f"Rebalancing: wide [{wide_lower:.2f}, {wide_upper:.2f}], "
                f"tight [{tight_lower:.2f}, {tight_upper:.2f}]"
            )
        else:
            logger.info("Keeping current positions")

        return self.positions

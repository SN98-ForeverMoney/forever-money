"""
Volatility-Aware Single-Position Miner (MinimalMiner)

This module implements a minimal LP strategy where a single liquidity position
recenters dynamically based on recent market volatility.

Key Features:
- Single LP position
- Rebalance triggered when price nears edges of current position
- Position width scales with recent price volatility
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
    Volatility-Aware Single-Position Miner.

    Maintains a single LP position that recenters when the market price
    approaches the edges of the position. Position width is proportional
    to recent market volatility.

    Attributes:
        inventory (Inventory): Current inventory for LP positions
        width_factor (float): Multiplier to scale position width with volatility
        volatility_window (int): Number of recent prices used to compute volatility
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
            width_factor (float, optional): Multiplier for volatility-based width
            volatility_window (int, optional): Number of recent prices for volatility
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
        Determine whether to rebalance and generate a new LP position.

        Logic:
        - If no positions exist, create initial position
        - Rebalance if price is within 20% of edges of current position
        - Position width scales with recent volatility

        Args:
            current_tick (int): Current market tick
            tick_spacing (int): Minimum tick spacing for the pool
            recent_prices (List[float]): List of recent ticks for volatility calculation

        Returns:
            List[Position]: Updated list of LP positions (single element)
        """
        should_rebalance = False

        if not self.positions:
            should_rebalance = True
        else:
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

            center_tick = (current_tick // tick_spacing) * tick_spacing
            lower_tick = int((center_tick - width) // tick_spacing * tick_spacing)
            upper_tick = int((center_tick + width) // tick_spacing * tick_spacing)

            new_pos = Position(
                tick_lower=lower_tick,
                tick_upper=upper_tick,
                allocation0=self.inventory.amount0,
                allocation1=self.inventory.amount1,
            )
            self.positions = [new_pos]
            logger.info(
                f"Rebalancing: new position [{lower_tick:.2f}, {upper_tick:.2f}]"
            )
        else:
            logger.info("Keeping current positions")

        return self.positions

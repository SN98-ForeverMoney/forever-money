"""
Baseline Single-Position Miner (MinimalMiner)

This module implements a minimal LP strategy where a single liquidity position
recenters based on a fixed width.

Key Features:
- Single LP position
- Rebalance triggered when price nears edges of current position
- Position width is fixed
"""

import logging
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

    def __init__(
        self,
        inventory: Inventory,
    ):
        """
        Initialize the miner with inventory and configuration.

        Args:
            inventory (Inventory): Initial token balances

        """
        self.inventory = inventory
        self.positions: List[Position] = []

        logger.info(f"Starting Miner V1 with inventory: {inventory}")

    def rebalance_query_handler(
        self,
        current_tick: int,
        tick_spacing: int,
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
            width = 2000  # Configurable width

            # Snap to tick spacing (ticks must be multiples of spacing)
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

"""
Volatility-Band Miner

This module implements a volatility-aware LP strategy that dynamically adjusts
liquidity positions based on recent price movements.

Key Features:
- Maintains a single LP position
- Rebalances when price drifts outside a volatility-defined band
- Position width scales with both current tick and recent volatility
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
    Volatility-Band Miner.

    Maintains a single liquidity position that recenters when price drifts outside
    a volatility-based band. The LP range width and rebalance thresholds are
    derived from recent price volatility.

    Attributes:
        inventory (Inventory): Current inventory for LP positions
        band_multiplier (float): Multiplier for price deviation to trigger rebalance
        lp_width_multiplier (float): Multiplier to scale LP width
        volatility_window (int): Number of recent prices used to compute volatility
        min_volatility (float): Minimum volatility floor to avoid overly narrow positions
    """

    def __init__(
        self,
        inventory: Inventory,
        band_multiplier: float = 4.0,
        lp_width_multiplier: float = 1.5,
        volatility_window: int = 10,
        min_volatility: float = 0.001,
    ):
        """
        Initialize the miner with inventory and configuration.

        Args:
            inventory (Inventory): Initial token balances
            band_multiplier (float, optional): Multiplier for volatility band
            lp_width_multiplier (float, optional): Multiplier to scale LP width
            volatility_window (int, optional): Number of recent prices for volatility
            min_volatility (float, optional): Minimum volatility floor
        """
        self.inventory = inventory
        self.positions: List[Position] = []
        self.band_multiplier = band_multiplier
        self.lp_width_multiplier = lp_width_multiplier
        self.min_volatility = min_volatility
        self.volatility_window = volatility_window
        logger.info(
            f"""Starting Miner V1 with inventory: {inventory}, 
            band multiplier: {band_multiplier}, 
            lp width multiplier: {lp_width_multiplier}, 
            min volatility: {min_volatility} and 
            volatility window: {volatility_window}"""
        )

    def compute_volatility(self, recent_prices: List[float]) -> float:
        """
        Compute price volatility from recent prices.

        Args:
            recent_prices (List[float]): List of recent price ticks

        Returns:
            float: Standard deviation of relative price changes, floored by min_volatility
        """
        if len(recent_prices) < self.volatility_window:
            return self.min_volatility

        price_changes = [
            (recent_prices[i] - recent_prices[i - 1]) / recent_prices[i - 1]
            for i in range(1, len(recent_prices))
        ]
        mean = sum(price_changes) / len(price_changes)
        variance = sum((x - mean) ** 2 for x in price_changes) / len(price_changes)
        return max(math.sqrt(variance), self.min_volatility)

    def rebalance_query_handler(
        self, current_tick: int, tick_spacing: int, recent_prices: List[float]
    ):
        """
        Determine whether to rebalance and generate a new LP position.

        Logic:
        - If no positions exist, create initial position
        - Rebalance if price deviates from current position center by more than a volatility band
        - Position width is scaled by lp_width_multiplier

        Args:
            current_tick (int): Current market tick
            tick_spacing (int): Minimum tick spacing for the pool
            recent_prices (List[float]): List of recent ticks for volatility calculation

        Returns:
            List[Position]: Updated list of LP positions (single element)
        """
        should_rebalance = False

        volatility = self.compute_volatility(recent_prices)
        band_width = current_tick * volatility * self.band_multiplier
        width = band_width * self.lp_width_multiplier

        if not self.positions:
            should_rebalance = True
        else:
            pos = self.positions[0]
            center_tick = (pos.tick_lower + pos.tick_upper) // 2

            if abs(current_tick - center_tick) > band_width:
                should_rebalance = True

        if should_rebalance:
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
                f"Rebalancing: new position [{lower_tick}, {upper_tick}] "
                f"center={center_tick}, volatility={volatility:.6f}"
            )

            return self.positions
        else:
            logger.info("Keeping current positions.")

        return self.positions

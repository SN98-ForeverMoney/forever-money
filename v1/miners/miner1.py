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
    # Use LP position where price ranges dynamically adjust based on recent market price changes (volatility)
    # Comparisions to miner v0
    # fixed width vs volatility based width
    # no market awareness vs market awareness
    def __init__(
        self,
        inventory: Inventory,
        width_factor: float = 3.0,
        volatility_window: int = 10,
    ):
        self.inventory = inventory
        self.positions: List[Position] = []
        self.width_factor = width_factor
        self.volatility_window = volatility_window
        logger.info(
            f"Starting Miner V1 with inventory: {inventory}, width factor: {width_factor} and volatility window: {volatility_window}"
        )

    def compute_volatility(self, recent_prices: List[float]) -> float:
        if len(recent_prices) < self.volatility_window:
            return 0.0

        price_changes = [
            (recent_prices[i] - recent_prices[i - 1]) / recent_prices[i - 1]
            for i in range(1, len(recent_prices))
        ]
        mean = sum(price_changes) / len(price_changes)
        variance = sum((x - mean) ** 2 for x in price_changes) / len(price_changes)
        return math.sqrt(variance)

    # recent_prices -> used to determine the volatility factor
    def rebalance_query_handler(
        self, current_tick: int, tick_spacing: int, recent_prices: List[float]
    ):
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

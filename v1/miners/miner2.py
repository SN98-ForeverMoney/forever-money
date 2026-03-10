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
    # Use a volatility-aware LP strategy that recenters liquidity when price drifts too far from the position center.
    # LP range width and rebalance thresholds are determined using recent market volatility.
    # Comparisons to miner v1
    # fixed buffer rebalance trigger vs volatility band rebalance trigger
    # simple volatility-scaled width vs full volatility-driven positioning logic
    def __init__(
        self,
        inventory: Inventory,
        band_multiplier: float = 4.0,
        lp_width_multiplier: float = 1.5,
        volatility_window: int = 10,
        min_volatility: float = 0.001,
    ):
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
        if len(recent_prices) < self.volatility_window:
            return self.min_volatility

        price_changes = [
            (recent_prices[i] - recent_prices[i - 1]) / recent_prices[i - 1]
            for i in range(1, len(recent_prices))
        ]
        mean = sum(price_changes) / len(price_changes)
        variance = sum((x - mean) ** 2 for x in price_changes) / len(price_changes)
        return max(math.sqrt(variance), self.min_volatility)

    # recent_prices -> used to determine the volatility factor
    def rebalance_query_handler(
        self, current_tick: int, tick_spacing: int, recent_prices: List[float]
    ):
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

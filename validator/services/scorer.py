import asyncio
import math
from typing import Dict, Any

from validator.utils.web3 import AsyncWeb3Helper
from validator.utils.math import UniswapV3Math
from validator.models.job import Job


class Scorer:

    @staticmethod
    async def score_pol_strategy(
        metrics: Dict[str, Any],
        loss_penalty_multiplier: float = 10.0,
        smooth_beta: float = 4.0,
    ) -> float:
        """
        Score strategy based on value gain (token1 units) with a smooth
        inventory-loss penalty.

        Args:
            metrics: Dict from Backtester with:
                - initial_value
                - final_value
                - initial_inventory (Inventory object)
                - final_inventory (Inventory object)
            loss_penalty_multiplier: Strength of inventory loss penalty
            smooth_beta: Controls how close loss aggregation is to max()
                         (lower = more sum-like, higher = more max-like)

        Returns:
            Final score (float)
        """

        # -----------------------------
        # Extract values
        # -----------------------------
        initial_total_value = metrics["initial_value"]
        final_total_value = metrics["final_value"]
        
        initial_inventory = metrics["initial_inventory"]
        final_inventory = metrics["final_inventory"]

        if initial_total_value <= 0:
            return float("-inf")

        # -----------------------------
        # Extract amounts from Inventory objects
        # -----------------------------
        # Inventory is a Pydantic model or similar object with amount0/amount1 fields (str or int)
        initial_amount0 = int(initial_inventory.amount0)
        initial_amount1 = int(initial_inventory.amount1)
        
        final_amount0 = int(final_inventory.amount0)
        final_amount1 = int(final_inventory.amount1)

        # -----------------------------
        # Raw inventory loss
        # -----------------------------
        amount0_loss = max(0, initial_amount0 - final_amount0)
        amount1_loss = max(0, initial_amount1 - final_amount1)

        # -----------------------------
        # Value gain (primary signal)
        # -----------------------------
        value_gain = float(final_total_value - initial_total_value)

        # -----------------------------
        # Relative inventory loss
        # -----------------------------
        loss_ratio0 = (
            amount0_loss / initial_amount0
            if initial_amount0 > 0
            else 0.0
        )
        loss_ratio1 = (
            amount1_loss / initial_amount1
            if initial_amount1 > 0
            else 0.0
        )

        # -----------------------------
        # Smooth-max (log-sum-exp)
        # -----------------------------
        m = max(loss_ratio0, loss_ratio1)
        inventory_loss_ratio = m + (1.0 / smooth_beta) * math.log(
            math.exp(smooth_beta * (loss_ratio0 - m))
            + math.exp(smooth_beta * (loss_ratio1 - m))
        )

        # -----------------------------
        # Exponential penalty
        # -----------------------------
        penalty_factor = math.exp(
            -loss_penalty_multiplier * inventory_loss_ratio
        )

        # -----------------------------
        # Symmetric penalty application
        # -----------------------------
        if value_gain >= 0:
            score = value_gain * penalty_factor
        else:
            score = value_gain / penalty_factor

        return score

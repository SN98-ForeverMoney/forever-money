import asyncio
import math
from typing import Dict, Any

from validator.utils.web3 import AsyncWeb3Helper
from validator.utils.math import UniswapV3Math
from validator.models.job import Job


class Scorer:

    @staticmethod
    async def score_pol_strategy(
        performance_metrics: Dict[str, Any],
        initial_inventory: Dict[str, Any],
        loss_penalty_multiplier: float = 10.0,
        smooth_beta: float = 4.0,
    ) -> float:
        """
        Score strategy based on value gain (token1 units) with a smooth
        inventory-loss penalty.

        - Primary signal: value gain at pool price
        - Inventory loss aggregated via smooth-max (log-sum-exp)
        - Penalty applies symmetrically:
            * reduces positive gains
            * amplifies negative losses
        - Produces a total ordering (always rankable)

        Args:
            performance_metrics: Dict with:
                - amount0_holdings
                - amount1_holdings
                - fees0
                - fees1
                - final_sqrt_price_x96
            initial_inventory: Dict with:
                - initial_amount0
                - initial_amount1
                - initial_sqrt_price_x96
            loss_penalty_multiplier: Strength of inventory loss penalty
            smooth_beta: Controls how close loss aggregation is to max()
                         (lower = more sum-like, higher = more max-like)

        Returns:
            Final score (float)
        """

        # -----------------------------
        # Extract initial inventory
        # -----------------------------
        initial_amount0 = initial_inventory["initial_amount0"]
        initial_amount1 = initial_inventory["initial_amount1"]
        initial_sqrt_price_x96 = initial_inventory["initial_sqrt_price_x96"]

        # -----------------------------
        # Extract final performance
        # -----------------------------
        final_amount0 = performance_metrics["amount0_holdings"]
        final_amount1 = performance_metrics["amount1_holdings"]
        fees0 = performance_metrics["fees0"]
        fees1 = performance_metrics["fees1"]
        final_sqrt_price_x96 = performance_metrics["final_sqrt_price_x96"]

        # -----------------------------
        # Raw inventory loss
        # -----------------------------
        amount0_loss = max(0, initial_amount0 - final_amount0)
        amount1_loss = max(0, initial_amount1 - final_amount1)

        # -----------------------------
        # Price math (Q192)
        # -----------------------------
        initial_price_x192 = initial_sqrt_price_x96 * initial_sqrt_price_x96
        final_price_x192 = final_sqrt_price_x96 * final_sqrt_price_x96

        # -----------------------------
        # Initial value (token1 units)
        # -----------------------------
        initial_value0_in_token1 = (
            initial_amount0 * initial_price_x192
        ) // UniswapV3Math.Q192

        initial_total_value = initial_value0_in_token1 + initial_amount1

        if initial_total_value <= 0:
            return float("-inf")

        # -----------------------------
        # Final value (token1 units, incl. fees)
        # -----------------------------
        final_value0_in_token1 = (
            final_amount0 * final_price_x192
        ) // UniswapV3Math.Q192

        fees_value0_in_token1 = (
            fees0 * final_price_x192
        ) // UniswapV3Math.Q192

        final_total_value = (
            final_value0_in_token1
            + final_amount1
            + fees_value0_in_token1
            + fees1
        )

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


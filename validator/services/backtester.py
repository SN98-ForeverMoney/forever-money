"""
Backtester for simulating LP strategy performance.

This module provides accurate simulation of Uniswap V3 / Aerodrome v3
concentrated liquidity positions, including:
- Proper fee calculation based on liquidity share
- Accurate impermanent loss computation
- Rebalance simulation following strategy rules
"""
import logging
from typing import List, Dict, Any, Tuple

from protocol import Position, Inventory
from validator.repositories.pool import DataSource
from validator.utils.math import UniswapV3Math

logger = logging.getLogger(__name__)


class BacktesterService:
    """
    Simulates LP strategy performance using historical pool events.
    Compares strategy performance against HODL baseline.

    Key improvements over naive implementation:
    - Calculates actual liquidity share per swap event
    - Simulates rebalances based on strategy rules
    - Uses pool-specific fee rates
    """

    def __init__(
        self,
        data_source: DataSource,
    ):
        """
        Initialize backtester.

        Args:
            data_source: Data source for historical data (implements DataSource interface)
        """
        self.db = data_source  # Keep as self.db for compatibility

    def _calculate_liquidity_share(
        self,
        simulated_in_range_liquidity: float,
        event: Dict[str, Any],
    ) -> float:
        """
        Calculate the share of fees this position earns from a swap.

        This is the key improvement: instead of assuming 1% share,
        we calculate the actual share based on:
        1. Position liquidity
        2. Total pool liquidity (from swap event)
        3. Whether price is in range

        Args:
            simulated_in_range_liquidity: Liquidity of the positions in range
            event: Swap event data

        Returns:
            Liquidity share (0.0 to 1.0)
        """
        # Get total pool liquidity from event (if available)
        pool_liquidity = event.get("liquidity")
        if pool_liquidity:
            pool_liquidity = float(pool_liquidity) + simulated_in_range_liquidity
        else:
            raise ValueError(f"Liquidity not available for event ${event.get('id')}")

        if pool_liquidity <= 0:
            logger.warning(
                f"Pool liquidity is <= 0 ({pool_liquidity}). "
                "This suggests bad data or a bug. Returning 0 share."
            )
            return 0.0

        # Calculate share (capped at 100% to handle edge cases)
        share = min(1.0, simulated_in_range_liquidity / pool_liquidity)
        return share

    def _simulate_fees_for_positions(
        self,
        positions: List[Position],
        swap_events: List[Dict[str, Any]],
        fee_rate: float,
    ) -> Tuple[float, float, int]:
        """
        Simulate fee accumulation for a set of static positions over swap events.

        Returns:
            (fees0, fees1, in_range_count)
        """
        total_fees0 = 0.0
        total_fees1 = 0.0
        in_range_count = 0

        for event in swap_events:
            sqrt_price_x96 = int(event.get("sqrt_price_x96"))
            total_in_range_liq = 0
            for position in positions:
                sqrt_price_lower_x96 = UniswapV3Math.get_sqrt_ratio_at_tick(
                    position.tick_lower
                )
                sqrt_price_upper_x96 = UniswapV3Math.get_sqrt_ratio_at_tick(
                    position.tick_upper
                )
                if sqrt_price_lower_x96 <= sqrt_price_x96 <= sqrt_price_upper_x96:
                    in_range_count += 1
                    (
                        position_liquidity,
                        _,
                        _,
                    ) = UniswapV3Math.position_liquidity_and_used_amounts(
                        position.tick_lower,
                        position.tick_upper,
                        sqrt_price_x96,
                        int(position.allocation0),
                        int(position.allocation1),
                    )
                    total_in_range_liq += position_liquidity

            raw_amount0 = float(event.get("amount0", 0) or 0)
            raw_amount1 = float(event.get("amount1", 0) or 0)

            liquidity_share = self._calculate_liquidity_share(
                total_in_range_liq, event,
            )

            if raw_amount0 > 0:
                total_fees0 += int(raw_amount0 * fee_rate * liquidity_share)
            elif raw_amount1 > 0:
                total_fees1 += int(raw_amount1 * fee_rate * liquidity_share)

        return total_fees0, total_fees1, in_range_count

    async def evaluate_positions_performance(
        self,
        pair_address: str,
        rebalance_history: List[Dict[str, Any]],
        start_block: int,
        end_block: int,
        initial_inventory: Inventory,
        fee_rate: float,
        baseline_positions: List[Position] = None,
    ) -> Dict[str, Any]:
        """
        Simulate a miner's LP positions over a block range using V3 concentrated liquidity math.

        Scores the miner's strategy against a baseline:
        - If baseline_positions is provided, the baseline is the performance of
          those positions (the current on-chain positions) over the same period.
        - Otherwise falls back to HODL of the initial inventory.

        Args:
            pair_address: Pool address
            rebalance_history: LP positions to simulate
            start_block: Starting block
            end_block: Ending block
            initial_inventory: The inventory at the start of the simulation.
            fee_rate: Fee rate for the pool
            baseline_positions: Current on-chain positions to use as baseline

        Returns:
            Dictionary with performance metrics including initial_value and final_value
        """

        # Handle empty rebalance history (miner returned no positions)
        if not rebalance_history:
            logger.warning(
                f"[BACKTESTER] Empty rebalance history for pair {pair_address} "
                f"(blocks {start_block}-{end_block}). Miner never rebalanced — "
                f"returning zero metrics (no initial_value/final_value → scorer will return SCORE_MIN)"
            )
            return {
                "fees_collected": 0,
                "impermanent_loss": 0.0,
                "fees0": 0,
                "fees1": 0,
                "in_range_ratio": 0.0,
                "amount0_deployed": 0,
                "amount1_deployed": 0,
                "amount0_holdings": int(initial_inventory.amount0),
                "amount1_holdings": int(initial_inventory.amount1),
                "final_sqrt_price_x96": 0,
            }

        rebalance_history.sort(key=lambda x: x["block"], reverse=True)

        def get_deployed_positions(current_block: int) -> List[Position]:
            """Get deployed positions for current block."""
            for rebalance in rebalance_history:
                if current_block > rebalance["block"]:
                    return rebalance["new_positions"]

            raise ValueError("Invalid rebalance history.")

        # Get swap events in this range
        swap_events = await self.db.get_swap_events(
            pair_address, start_block, end_block
        )

        # Track miner's fees
        total_fees0 = 0.0
        total_fees1 = 0.0
        in_range_count = 0
        total_swaps = len(swap_events)
        logger.info(f"Got {total_swaps} swaps for pair {pair_address}.")

        # Simulate each swap for fee accumulation (miner's strategy)
        for event in swap_events:
            sqrt_price_x96 = int(event.get("sqrt_price_x96"))
            block_number = int(event.get("block_number"))
            positions = get_deployed_positions(block_number)
            total_in_range_liq = 0
            for position in positions:
                sqrt_price_lower_x96 = UniswapV3Math.get_sqrt_ratio_at_tick(
                    position.tick_lower
                )
                sqrt_price_upper_x96 = UniswapV3Math.get_sqrt_ratio_at_tick(
                    position.tick_upper
                )

                if sqrt_price_lower_x96 <= sqrt_price_x96 <= sqrt_price_upper_x96:
                    in_range_count += 1
                    (
                        position_liquidity,
                        amount0_deployed,
                        amount1_deployed,
                    ) = UniswapV3Math.position_liquidity_and_used_amounts(
                        position.tick_lower,
                        position.tick_upper,
                        sqrt_price_x96,
                        int(position.allocation0),
                        int(position.allocation1),
                    )
                    total_in_range_liq += position_liquidity

            raw_amount0 = float(event.get("amount0", 0) or 0)
            raw_amount1 = float(event.get("amount1", 0) or 0)

            liquidity_share = self._calculate_liquidity_share(
                total_in_range_liq,
                event,
            )

            if raw_amount0 > 0:
                total_fees0 += int(raw_amount0 * fee_rate * liquidity_share)
            elif raw_amount1 > 0:
                total_fees1 += int(raw_amount1 * fee_rate * liquidity_share)

        final_sqrt_price_x96 = await self.db.get_sqrt_price_at_block(
            pair_address, end_block
        )
        initial_sqrt_price_x96 = await self.db.get_sqrt_price_at_block(
            pair_address, start_block
        )
        if final_sqrt_price_x96 is None:
            raise ValueError(
                f"Cannot get sqrt price at end_block={end_block} for pair {pair_address}"
            )
        if initial_sqrt_price_x96 is None:
            raise ValueError(
                f"Cannot get sqrt price at start_block={start_block} for pair {pair_address}"
            )

        # price in Q192 (token1/token0)
        final_price_x192 = final_sqrt_price_x96 * final_sqrt_price_x96
        initial_price_x192 = initial_sqrt_price_x96 * initial_sqrt_price_x96

        # Miner's final deployed amounts
        amount0_deployed, amount1_deployed = 0, 0
        for position in rebalance_history[0]["new_positions"]:
            _, amount0, amount1 = UniswapV3Math.position_liquidity_and_used_amounts(
                position.tick_lower,
                position.tick_upper,
                final_sqrt_price_x96,
                int(position.allocation0),
                int(position.allocation1),
            )
            amount0_deployed += int(amount0)
            amount1_deployed += int(amount1)

        final_inventory: Inventory = rebalance_history[0]["inventory"]

        # Miner's LP value (deployed + idle)
        amount0_holdings = amount0_deployed + int(final_inventory.amount0)
        amount1_holdings = amount1_deployed + int(final_inventory.amount1)
        lp_value = (
            amount0_holdings * final_price_x192
        ) // UniswapV3Math.Q192 + amount1_holdings

        # Miner's fees (valued in token1 units)
        fees_collected = (
            total_fees0 * final_price_x192
        ) // UniswapV3Math.Q192 + total_fees1

        # Miner's final value
        final_value = lp_value + fees_collected

        # --- Baseline: current on-chain position performance ---
        if baseline_positions and len(baseline_positions) > 0:
            # Simulate baseline positions over the same swaps
            baseline_fees0, baseline_fees1, _ = self._simulate_fees_for_positions(
                baseline_positions, swap_events, fee_rate,
            )

            # Baseline deployed amounts at final price
            baseline_a0_deployed, baseline_a1_deployed = 0, 0
            for pos in baseline_positions:
                _, a0, a1 = UniswapV3Math.position_liquidity_and_used_amounts(
                    pos.tick_lower, pos.tick_upper, final_sqrt_price_x96,
                    int(pos.allocation0), int(pos.allocation1),
                )
                baseline_a0_deployed += int(a0)
                baseline_a1_deployed += int(a1)

            # Baseline idle = initial inventory minus what baseline deployed at start
            baseline_a0_deployed_start, baseline_a1_deployed_start = 0, 0
            for pos in baseline_positions:
                _, a0, a1 = UniswapV3Math.position_liquidity_and_used_amounts(
                    pos.tick_lower, pos.tick_upper, initial_sqrt_price_x96,
                    int(pos.allocation0), int(pos.allocation1),
                )
                baseline_a0_deployed_start += int(a0)
                baseline_a1_deployed_start += int(a1)

            baseline_idle0 = int(initial_inventory.amount0) - baseline_a0_deployed_start
            baseline_idle1 = int(initial_inventory.amount1) - baseline_a1_deployed_start
            baseline_idle0 = max(0, baseline_idle0)
            baseline_idle1 = max(0, baseline_idle1)

            baseline_a0_total = baseline_a0_deployed + baseline_idle0
            baseline_a1_total = baseline_a1_deployed + baseline_idle1

            baseline_lp_value = (
                baseline_a0_total * final_price_x192
            ) // UniswapV3Math.Q192 + baseline_a1_total

            baseline_fees = (
                baseline_fees0 * final_price_x192
            ) // UniswapV3Math.Q192 + baseline_fees1

            initial_value = baseline_lp_value + baseline_fees

            logger.info(
                f"[BACKTESTER] Baseline (current position): value={initial_value}, "
                f"fees=({baseline_fees0:.0f}, {baseline_fees1:.0f}), "
                f"deployed=({baseline_a0_deployed}, {baseline_a1_deployed}), "
                f"idle=({baseline_idle0}, {baseline_idle1})"
            )
        else:
            # Fallback: HODL baseline
            initial_value = (
                int(initial_inventory.amount0) * initial_price_x192
            ) // UniswapV3Math.Q192 + int(initial_inventory.amount1)

        logger.info(
            f"[BACKTESTER] Miner: final_value={final_value}, lp_value={lp_value}, "
            f"fees=({total_fees0:.0f}, {total_fees1:.0f}), "
            f"deployed=({amount0_deployed}, {amount1_deployed}), "
            f"baseline_initial_value={initial_value}"
        )

        # Impermanent loss (relative to HODL — kept for penalty calculation)
        hodl_value = (
            int(initial_inventory.amount0) * final_price_x192
        ) // UniswapV3Math.Q192 + int(initial_inventory.amount1)
        if hodl_value > 0:
            impermanent_loss = max(
                0.0,
                float(hodl_value - lp_value) / float(hodl_value),
            )
        else:
            impermanent_loss = 0.0

        # In-range ratio
        in_range_ratio = in_range_count / total_swaps if total_swaps > 0 else 0.0

        return {
            "fees_collected": fees_collected,
            "impermanent_loss": impermanent_loss,
            "fees0": total_fees0,
            "fees1": total_fees1,
            "in_range_ratio": in_range_ratio,
            "amount0_deployed": amount0_deployed,
            "amount1_deployed": amount1_deployed,
            "amount0_holdings": amount0_holdings,
            "amount1_holdings": amount1_holdings,
            "final_sqrt_price_x96": final_sqrt_price_x96,
            "initial_value": initial_value,
            "final_value": final_value,
            "initial_inventory": initial_inventory,
            "final_inventory": final_inventory,
        }

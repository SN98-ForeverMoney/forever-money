"""
Backtester for simulating LP strategy performance.

This module provides accurate simulation of Uniswap V3 / Aerodrome v3
concentrated liquidity positions, including:
- Proper fee calculation based on liquidity share
- Accurate impermanent loss computation
- Rebalance simulation following strategy rules
"""
import logging
from typing import List, Dict, Any

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

    async def evaluate_positions_performance(
        self,
        pair_address: str,
        rebalance_history: List[Dict[str, Any]],
        start_block: int,
        end_block: int,
        initial_inventory: Inventory,
        fee_rate: float,
    ) -> Dict[str, Any]:
        """
        Simulate a single LP position over a block range using V3 concentrated liquidity math.

        Args:
            pair_address: Pool address
            rebalance_history: LP positions to simulate
            start_block: Starting block
            end_block: Ending block
            initial_inventory: The inventory at the start of the simulation.
            fee_rate: Fee rate for the pool

        Returns:
            Dictionary containing:
            - fees_collected: Total fees earned (in token1 terms)
            - final_amount0: Amount of token0 at end
            - final_amount1: Amount of token1 at end
            - impermanent_loss: IL as fraction (0.0 to 1.0)
            - fees0: Fees in token0
            - fees1: Fees in token1
            - in_range_ratio: Fraction of time price was in range
        """

        # Handle empty rebalance history (miner returned no positions)
        if not rebalance_history:
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
                "final_sqrt_price_x96": 0,  # Will be updated below if needed
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

        # Track fees
        total_fees0 = 0.0
        total_fees1 = 0.0
        in_range_count = 0
        total_swaps = len(swap_events)

        # Simulate each swap for fee accumulation
        for event in swap_events:
            # Calculate price from sqrt_price_x96 if available
            sqrt_price_x96 = int(event.get("sqrt_price_x96"))
            block_number = event.get("evt_block_number")
            positions = get_deployed_positions(block_number)
            total_in_range_liq = 0
            for position in positions:
                # Convert tick bounds to prices
                sqrt_price_lower_x96 = UniswapV3Math.get_sqrt_ratio_at_tick(
                    position.tick_lower
                )
                sqrt_price_upper_x96 = UniswapV3Math.get_sqrt_ratio_at_tick(
                    position.tick_upper
                )

                # Check if position is in range
                if sqrt_price_lower_x96 <= sqrt_price_x96 <= sqrt_price_upper_x96:
                    in_range_count += 1
                    # Calculate position liquidity AND actual amounts deployed
                    # In V3, you can't always deploy all tokens - only what fits the limiting token
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

            # Get swap amounts (signed: positive = token came IN, negative = token went OUT)
            # In Uniswap V3, fees are ONLY charged on the INPUT token
            raw_amount0 = float(event.get("amount0", 0) or 0)
            raw_amount1 = float(event.get("amount1", 0) or 0)

            # Calculate liquidity share for this swap
            liquidity_share = self._calculate_liquidity_share(
                total_in_range_liq,
                event,
            )

            # Fees earned ONLY on the input token (the one with positive amount)
            # If amount0 > 0: user swapped token0 for token1, fee is on token0
            # If amount1 > 0: user swapped token1 for token0, fee is on token1
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

        # price in Q192 (token1/token0)
        final_price_x192 = (
            final_sqrt_price_x96 * final_sqrt_price_x96
        )  # equivalent of final_sqrt_price_x96 ^ 2
        initial_price_x192 = (
            initial_sqrt_price_x96 * initial_sqrt_price_x96
        )

        # get amounts currently in pool
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

        # HODL value (token1 units, int-only)
        # IMPORTANT: HODL uses INITIAL inventory, valued at FINAL price
        hodl_value_deployed = (
            int(initial_inventory.amount0) * final_price_x192
        ) // UniswapV3Math.Q192 + int(initial_inventory.amount1)

        # LP value (token1 units, int-only)
        # deployed + idle
        amount0_holdings = amount0_deployed + int(final_inventory.amount0)
        amount1_holdings = amount1_deployed + int(final_inventory.amount1)

        lp_value_deployed = (
            amount0_holdings * final_price_x192
        ) // UniswapV3Math.Q192 + amount1_holdings

        # Fees (valued in token1 units)
        fees_collected = (
            total_fees0 * final_price_x192
        ) // UniswapV3Math.Q192 + total_fees1
        
        # Initial Value (at start price)
        initial_value = (
            int(initial_inventory.amount0) * initial_price_x192
        ) // UniswapV3Math.Q192 + int(initial_inventory.amount1)
        
        # Final Value (LP + Fees)
        final_value = lp_value_deployed + fees_collected

        # Impermanent loss (ratio, float only at the very end)
        if hodl_value_deployed > 0:
            impermanent_loss = max(
                0.0,
                float(hodl_value_deployed - lp_value_deployed)
                / float(hodl_value_deployed),
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

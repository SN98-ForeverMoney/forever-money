"""
Backtester for simulating LP strategy performance using historical pool events.

This module provides a simplified simulation environment for testing liquidity
provision strategies, including:

- Fee calculation based on liquidity share
- Impermanent loss evaluation
- Strategy-specific rebalance simulation

Designed for research and testing not production use.
"""

import logging
from typing import List, Dict
from protocol.models import Inventory
from validator.services.backtester import BacktesterService
from validator.repositories.pool import DataSource


# Configure logging
logging.basicConfig(
    filename="output.log",
    filemode="w",
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class MinimalBacktester:
    """
    Minimal backtester for liquidity provision strategies.

    This class simulates LP strategy performance using historical swap events
    and records the performance for later scoring against a HODL baseline.

    Attributes:
        backtester (BacktesterService): Service handling detailed performance evaluation.
        data_source (DataSource): Source for historical swap and pool data.
    """

    def __init__(
        self,
        data_source: DataSource,
    ):
        """
        Initialize the MinimalBacktester.

        Args:
            data_source (DataSource): Historical market data provider. Must implement
                                      the DataSource interface for swap events retrieval.
        """
        self.backtester = BacktesterService(data_source)
        self.data_source = data_source
        logger.info("Starting Backtester V1 with minimal data_source for test")

    async def run_backtest(
        self,
        rebalance_history: List[Dict],
        initial_inventory: Inventory,
        pair_address: str,
    ):
        """
        Run the backtest simulation.

        Steps:
        1. Fetch swap events from the data source
        2. Evaluate the strategy's performance across all rebalance events
        3. Return a structured results dictionary for scoring

        Args:
            rebalance_history (List[Dict]): List of rebalance events containing:
                - block number
                - new_positions
                - inventory
            initial_inventory (Inventory): Initial token balances for the strategy

        Returns:
            Dict: Contains metrics such as total value, fees earned, in-range ratio, etc.
        """
        start_block = rebalance_history[0]["block"]
        end_block = rebalance_history[-1]["block"]

        swap_events = await self.data_source.get_swap_events(
            pair_address=pair_address,
            start_block=start_block,
            end_block=end_block,
        )

        results = await self.backtester.evaluate_positions_performance(
            pair_address=pair_address,
            rebalance_history=rebalance_history,
            start_block=swap_events[0]["block_number"],
            end_block=swap_events[-1]["block_number"],
            initial_inventory=initial_inventory,
            fee_rate=0.003,
        )
        logger.info(f"{results=}")
        return results

    async def run_backtest_in_chunks(
        self,
        rebalance_history: List[Dict],
        initial_inventory: Inventory,
        pair_address: str,
        chunk_size: int = 10_000,
    ):
        """
        Run backtest in chunks, aggregating results.
        Uses evaluate_positions_performance for each chunk.

        Ensures initial_value and final_value are computed correctly.
        """
        if not rebalance_history:
            raise ValueError("Empty rebalance history")

        # Sort rebalance history by block ascending
        rebalance_history = sorted(rebalance_history, key=lambda r: r["block"])

        start_block = rebalance_history[0]["block"]
        end_block = rebalance_history[-1]["block"]

        # Initialize final results
        final_results = {
            "fees_collected": 0,
            "fees0": 0,
            "fees1": 0,
            "in_range_total": 0,
            "swap_total": 0,
            "amount0_deployed": 0,
            "amount1_deployed": 0,
            "amount0_holdings": 0,
            "amount1_holdings": 0,
            "final_sqrt_price_x96": 0,
            "initial_value": 0,
            "final_value": 0,
            "impermanent_loss": 0.0,
            "initial_inventory": initial_inventory,
            "final_inventory": initial_inventory,
        }

        previous_inventory = initial_inventory

        for chunk_start in range(start_block, end_block + 1, chunk_size):
            chunk_end = min(chunk_start + chunk_size - 1, end_block)

            # Select rebalance events up to the end of this chunk
            chunk_rebalances = [r for r in rebalance_history if r["block"] <= chunk_end]

            # Ensure there is at least one rebalance before chunk_start for continuity
            if not chunk_rebalances or chunk_rebalances[0]["block"] > chunk_start:
                # Insert a dummy rebalance at chunk_start using previous inventory
                chunk_rebalances.insert(
                    0,
                    {
                        "block": chunk_start,
                        "new_positions": [],
                        "inventory": previous_inventory,
                    },
                )

            chunk_results = await self.backtester.evaluate_positions_performance(
                pair_address=pair_address,
                rebalance_history=chunk_rebalances,
                start_block=chunk_start,
                end_block=chunk_end,
                initial_inventory=previous_inventory,
                fee_rate=0.003,
            )

            final_results["fees_collected"] += chunk_results.get("fees_collected", 0)
            final_results["fees0"] += chunk_results.get("fees0", 0)
            final_results["fees1"] += chunk_results.get("fees1", 0)
            final_results["amount0_deployed"] = chunk_results.get("amount0_deployed", 0)
            final_results["amount1_deployed"] = chunk_results.get("amount1_deployed", 0)
            final_results["amount0_holdings"] = chunk_results.get("amount0_holdings", 0)
            final_results["amount1_holdings"] = chunk_results.get("amount1_holdings", 0)
            final_results["final_sqrt_price_x96"] = chunk_results.get(
                "final_sqrt_price_x96", final_results["final_sqrt_price_x96"]
            )
            final_results["in_range_total"] += chunk_results.get(
                "in_range_ratio", 0
            ) * chunk_results.get("swap_total", 0)
            final_results["swap_total"] += chunk_results.get("swap_total", 0)

            # Keep last inventory for next chunk continuity
            previous_inventory = chunk_results.get(
                "final_inventory", previous_inventory
            )

            if final_results["initial_value"] == 0:
                final_results["initial_value"] = chunk_results.get("initial_value", 0)

        final_results["final_value"] = (
            final_results["amount0_holdings"]
            + final_results["amount1_holdings"]
            + final_results["fees_collected"]
        )

        if final_results["swap_total"] > 0:
            final_results["in_range_ratio"] = (
                final_results["in_range_total"] / final_results["swap_total"]
            )
        else:
            final_results["in_range_ratio"] = 0.0

        final_results.pop("in_range_total")
        final_results.pop("swap_total")

        # Final inventory is from last chunk
        final_results["final_inventory"] = previous_inventory

        return final_results

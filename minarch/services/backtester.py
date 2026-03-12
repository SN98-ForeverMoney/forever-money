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
        swap_events = await self.data_source.get_swap_events(
            pair_address="0xdummy",
            start_block=0,
            end_block=999999999,
        )

        results = await self.backtester.evaluate_positions_performance(
            pair_address="0xdummy",
            rebalance_history=rebalance_history,
            start_block=swap_events[0]["evt_block_number"],
            end_block=swap_events[-1]["evt_block_number"],
            initial_inventory=initial_inventory,
            fee_rate=0.003,
        )
        logger.info(f"{results=}")
        return results

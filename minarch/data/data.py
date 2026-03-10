"""
A minimal in-memory data source for testing liquidity provision strategies.

This module provides a simple abstraction to mimic historical pool data
for miners/backtesters. Designed for simulation and research purposes, not
production use.

Features:
- Holds a list of swap events in memory
- Returns swap events for any block range
- Returns the sqrt_price at a given block (simplified)
"""

import logging
from typing import List, Dict


# Configure logging
logging.basicConfig(
    filename="output.log",
    filemode="w",
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class MinimalData:
    """
    Minimal in-memory data provider for backtesting LP strategies.

    Attributes:
        swap_events (List[Dict]): List of swap events loaded in memory.
    """

    def __init__(self, swap_events: List[Dict]):
        self.swap_events = swap_events
        logger.info(
            f"Configured Datasource V1 with {len(swap_events)} swap events as test"
        )

    async def get_swap_events(self, pair_address, start_block, end_block):
        """
        Return swap events for the given pair and block range.

        Simplified: returns all swap events regardless of pair or block range.

        Args:
            pair_address (str): Address of the liquidity pair (ignored in minimal version)
            start_block (int): Starting block number (ignored)
            end_block (int): Ending block number (ignored)

        Returns:
            List[Dict]: List of swap event dictionaries
        """
        return self.swap_events

    async def get_sqrt_price_at_block(self, pair_address, block_number):
        """
        Return the sqrt_price at a given block.

        Simplified logic:
        - Returns the first swap event price if block <= first block
        - Returns the last swap event price otherwise

        Args:
            pair_address (str): Address of the liquidity pair (ignored in minimal version)
            block_number (int): Block number to query

        Returns:
            int: sqrt_price_x96 from the relevant swap event
        """
        if block_number <= self.swap_events[0]["evt_block_number"]:
            return self.swap_events[0]["sqrt_price_x96"]
        return self.swap_events[-1]["sqrt_price_x96"]

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
    def __init__(self, swap_events: List[Dict]):
        self.swap_events = swap_events
        logger.info(
            f"Configured Datasource V1 with {len(swap_events)} swap events as test"
        )

    async def get_swap_events(self, pair_address, start_block, end_block):
        return self.swap_events

    async def get_sqrt_price_at_block(self, pair_address, block_number):
        if block_number <= self.swap_events[0]["evt_block_number"]:
            return self.swap_events[0]["sqrt_price_x96"]
        return self.swap_events[-1]["sqrt_price_x96"]

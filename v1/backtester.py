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
    def __init__(
        self,
        data_source: DataSource,
    ):
        self.backtester = BacktesterService(data_source)
        self.data_source = data_source
        logger.info("Starting Backtester V1 with minimal data_source for test")

    async def run_backtest(
        self,
        rebalance_history: List[Dict],
        initial_inventory: Inventory,
    ):
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
        print(f"{results=}")
        return results

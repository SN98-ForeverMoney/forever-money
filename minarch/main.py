"""
Example entry point for running a liquidity strategy simulation using the minarch framework.

This script demonstrates:
- Loading swap event data
- Initializes a miner strategy (choose one: baseline, volatility-aware single-position,
  multi-position, or volatility-band)
- Rebalancing LP positions based on market events based on the selected strategy
- Running a minimal backtester
- Scoring the strategy relative to a HODL baseline

Notes:
- Only one miner strategy import should be active at a time.
- Outputs all logs to 'output.log'.
- Adjust block_chunk and block range to control simulation granularity.
"""

import asyncio
import logging
from itertools import groupby
from protocol.models import Inventory

# Baseline Miner
# from minarch.miners.baseline_miner import MinimalMiner
# Volatility-aware single-position strategy
from minarch.miners.volatility_miner import MinimalMiner

# Volatility-band adaptive strategy
# from minarch.miners.volatility_band_miner import MinimalMiner
# Multi-position 70/30 strategy
# from minarch.miners.multi_positions_miner import MinimalMiner
from minarch.data.data import MinimalData
from minarch.services.backtester import MinimalBacktester
from minarch.services.scorer import MinimalScorer


# Configure logging
logging.basicConfig(
    filename="output.log",
    filemode="w",
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


initial_inventory = Inventory(
    amount0="100000000000000000000",
    amount1="100000000000000000000",
)
block_chunk = 10_000
# eth/usdc pool
pair_address = "0xb2cc224c1c9fee385f8ad6a55b4d94e92359dc59"
# around 1 months of block data
start_block = 41925600
end_block = 43221600


# Required for backtester
rebalance_history = [
    {
        "block": start_block - 100,
        "new_positions": [],
        "inventory": initial_inventory,
    }
]


async def main():
    """
    Run the liquidity strategy simulation.

    Workflow:
    1. Initialize the miner with starting inventory.
    2. Load swap event data in block chunks.
    3. For each block, determine current price and query the miner
       for any required rebalances.
    4. Record rebalance history per block for backtesting.
    5. Run the backtester to evaluate the strategy across all chunks.
    6. Score the strategy against a HODL baseline using MinimalScorer.

    Returns:
        None. Results and logs are saved to 'output.log'.
    """
    miner = MinimalMiner(inventory=initial_inventory)

    data = await MinimalData.create()
    backtester = MinimalBacktester(data)

    for chunk_start in range(start_block, end_block, block_chunk):
        chunk_end = min(chunk_start + block_chunk - 1, end_block)
        logger.info(f"Loading swap events from {chunk_start} to {chunk_end}")

        swap_events = await data.get_swap_events(pair_address, chunk_start, chunk_end)

        if not swap_events:
            logger.info(
                f"No swaps for blocks {chunk_start} to {chunk_end}, skipping chunk"
            )
            continue

        for block, events in groupby(swap_events, key=lambda x: x["block_number"]):
            block_events = list(events)
            # last tick of the block is used as price
            price = int(block_events[-1]["tick"])
            tick_spacing = 60.0
            pos = miner.rebalance_query_handler(price, tick_spacing)

            # record rebalance history per block
            rebalance_history.append(
                {
                    "block": block,
                    "new_positions": pos,
                    "inventory": initial_inventory,
                }
            )

    results = await backtester.run_backtest_in_chunks(
        rebalance_history, initial_inventory, pair_address, block_chunk
    )
    logger.info(f"{results=}")

    scorer = MinimalScorer()
    score = await scorer.score_pol_strategy(results)
    logger.info(f"Score is: {score}")


if __name__ == "__main__":
    asyncio.run(main())

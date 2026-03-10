import asyncio
import logging
import os
import json
from itertools import groupby
from protocol.models import Inventory
from v1.miners.miner1 import MinimalMiner
from v1.data import MinimalData
from v1.backtester import MinimalBacktester
from v1.scorer import MinimalScorer


# Configure logging
logging.basicConfig(
    filename="output.log",
    filemode="w",
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


swap_events = []
filepath = os.path.join(os.getcwd(), "v1", "data", "base_poocl_swaps_v2.json")
with open(filepath, "r", encoding="utf-8") as swap_data:
    swap_events = json.load(swap_data)["data"]

logger.info(f"Loaded {len(swap_events)} swap events for testing")


initial_inventory = Inventory(
    amount0="100000000000000000000",
    amount1="100000000000000000000",
)


recent_prices = []
# Required for backtester
rebalance_history = [
    {
        "block": 0,
        "new_positions": [],
        "inventory": initial_inventory,
    }
]
miner = MinimalMiner(inventory=initial_inventory, volatility_window=5)

# for i, event in enumerate(swap_events):
#     price = int(event["tick"])
#     # for 0.3% tokens
#     tick_spacing = 60.0
#     recent_prices.append(price)
#     pos = miner.rebalance_query_handler(price, tick_spacing, recent_prices)

#     rebalance_history.append(
#         {
#             "block": event["evt_block_number"],
#             "new_positions": pos,
#             "inventory": initial_inventory,
#         }
#     )

for block, events in groupby(swap_events, key=lambda x: x["evt_block_number"]):
    block_events = list(events)
    # last tick of the block is used as price
    price = int(block_events[-1]["tick"])
    recent_prices.append(price)
    tick_spacing = 60.0
    pos = miner.rebalance_query_handler(price, tick_spacing, recent_prices)

    # record rebalance history per block
    rebalance_history.append(
        {
            "block": block,
            "new_positions": pos,
            "inventory": initial_inventory,
        }
    )


data = MinimalData(swap_events)
backtester = MinimalBacktester(data)
results = asyncio.run(backtester.run_backtest(rebalance_history, initial_inventory))

scorer = MinimalScorer()
score = asyncio.run(scorer.score_pol_strategy(results))
logger.info(f"Score is: {score}")

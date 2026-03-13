import logging
import os
from typing import List, Dict
from tortoise import Tortoise
from validator.models.pool_events import SwapEvent

DATABASE_URL = os.getenv("DATABASE_URL")

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
    Minimal DB-backed data provider for backtesting LP strategies.
    Each query fetches swap events directly from Postgres for a given block range.
    """

    def __init__(self):
        self.swap_events: List[Dict] = []

    @classmethod
    async def create(cls):
        """
        Async factory to initialize DB connection.
        Returns a MinimalData instance.
        """
        instance = cls()
        await Tortoise.init(
            db_url=DATABASE_URL,
            modules={"models": ["validator.models.pool_events"]},
        )
        logger.info("Initialized MinimalData DB connection")
        return instance

    async def get_swap_events(
        self, pair_address: str, start_block: int, end_block: int
    ):
        """
        Fetch swap events for the pair and block range directly from the database.
        """
        clean_address = pair_address.lower().replace("0x", "")
        events = (
            await SwapEvent.filter(
                evt_address=clean_address,
                evt_block_number__gte=start_block,
                evt_block_number__lte=end_block,
            )
            .order_by("evt_block_number")
            .values(
                "evt_address",
                "evt_block_number",
                "evt_tx_hash",
                "evt_block_time",
                "sqrt_price_x96",
                "tick",
                "amount0",
                "amount1",
                "liquidity",
                "sender",
                "recipient",
            )
        )

        swap_events = [
            {
                "block_number": e["evt_block_number"],
                "transaction_hash": e["evt_tx_hash"],
                "timestamp": e["evt_block_time"],
                "sqrt_price_x96": e["sqrt_price_x96"],
                "tick": e["tick"],
                "amount0": e["amount0"],
                "amount1": e["amount1"],
                "liquidity": e["liquidity"],
                "sender": e["sender"],
                "recipient": e["recipient"],
            }
            for e in events
        ]

        logger.info(
            f"Loaded {len(swap_events)} swap events from DB for blocks {start_block} to {end_block}"
        )
        return swap_events

    async def get_sqrt_price_at_block(self, pair_address: str, block_number: int):
        """
        Returns the sqrt_price_x96 at the given block.
        Simplified: takes the last swap <= block_number.
        """
        clean_address = pair_address.lower().replace("0x", "")
        event = (
            await SwapEvent.filter(
                evt_address=clean_address,
                evt_block_number__lte=block_number,
            )
            .order_by("-evt_block_number")
            .first()
        )
        if event is None:
            raise ValueError(f"No swap events found for block <= {block_number}")
        return int(event.sqrt_price_x96)

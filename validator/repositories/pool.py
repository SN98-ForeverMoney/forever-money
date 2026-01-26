"""
Database utilities for querying pool events from Postgres.
Adapted for the actual substreams schema with separate tables for swaps, mints, burns, etc.

Uses Tortoise ORM for async database operations.
Includes retry logic for transient failures.
"""
import asyncio
import logging
from abc import ABC, abstractmethod
from functools import wraps
from typing import List, Dict, Any, Optional

from tortoise import Tortoise
from tortoise.exceptions import DBConnectionError, OperationalError
from tortoise.functions import Sum

from validator.models.pool_events import SwapEvent, MintEvent, BurnEvent, CollectEvent

logger = logging.getLogger(__name__)


class DataSource(ABC):
    """
    Abstract base class for pool data sources.

    This allows the backtester and other components to work with
    different data sources (database, API, CSV files, etc.) without
    being tightly coupled to a specific implementation.

    All methods are async to support async database operations.
    """

    @abstractmethod
    async def get_swap_events(
        self,
        pair_address: str,
        start_block: Optional[int] = None,
        end_block: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch swap events for a specific pair within a block range."""
        pass

    @abstractmethod
    async def get_sqrt_price_at_block(
        self, pair_address: str, block_number: int
    ) -> Optional[int]:
        """Get the sqrt price at a specific block."""
        pass

    @abstractmethod
    async def get_mint_events(
        self,
        pair_address: str,
        start_block: Optional[int] = None,
        end_block: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch mint (liquidity addition) events."""
        pass

    @abstractmethod
    async def get_burn_events(
        self,
        pair_address: str,
        start_block: Optional[int] = None,
        end_block: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch burn (liquidity removal) events."""
        pass

    @abstractmethod
    async def get_collect_events(
        self,
        pair_address: str,
        start_block: Optional[int] = None,
        end_block: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch collect (fee collection) events."""
        pass

    @abstractmethod
    async def get_fee_growth(
        self, pair_address: str, start_block: int, end_block: int
    ) -> Dict[str, float]:
        """Calculate fee growth between two blocks."""
        pass

    @abstractmethod
    async def get_tick_at_block(
        self, pair_address: str, block_number: int
    ) -> Optional[int]:
        """Get the current tick at a specific block."""
        pass


# Retry configuration
MAX_RETRIES = 3
RETRY_DELAY_BASE = 1.0  # Base delay in seconds (exponential backoff)
RETRYABLE_ERRORS = (
    DBConnectionError,
    OperationalError,
    ConnectionError,
    TimeoutError,
)


def retry_on_db_error(func):
    """
    Decorator that retries async database operations on transient failures.
    Uses exponential backoff.
    """

    @wraps(func)
    async def wrapper(*args, **kwargs):
        last_exception = None
        for attempt in range(MAX_RETRIES):
            try:
                return await func(*args, **kwargs)
            except RETRYABLE_ERRORS as e:
                last_exception = e
                delay = RETRY_DELAY_BASE * (2**attempt)
                logger.warning(
                    f"Database operation failed (attempt {attempt + 1}/{MAX_RETRIES}): {e}. "
                    f"Retrying in {delay:.1f}s..."
                )
                await asyncio.sleep(delay)
            except Exception as e:
                # Non-retryable error, raise immediately
                raise
        # All retries exhausted
        logger.error(f"Database operation failed after {MAX_RETRIES} attempts")
        raise last_exception

    return wrapper


class PoolDataDB(DataSource):
    """
    Tortoise ORM implementation of the DataSource interface.

    Interface to the read-only Postgres database containing pool events.
    The database is fed by a subgraph and contains all on-chain events
    for Aerodrome pools (swaps, mints, burns, fee growth, etc.).

    Note: This database uses separate tables for each event type:
    - swaps: swap events with sqrt_price_x96, tick, amounts
    - mints: liquidity additions with tick ranges
    - burns: liquidity removals
    - collects: fee collections

    Features:
    - Automatic retry on transient connection failures
    - Uses Tortoise ORM for async database operations
    - Connection pooling handled by Tortoise

    Note: Tortoise ORM must be initialized before using this class.
    Call init_pool_events_db() or Tortoise.init() with appropriate config.
    """

    def __init__(self):
        """
        Initialize PoolDataDB.

        No connection parameters needed - Tortoise ORM handles connections.
        Make sure to call init_pool_events_db() before using this class.
        """
        pass

    @retry_on_db_error
    async def get_swap_events(
        self,
        pair_address: str,
        start_block: Optional[int] = None,
        end_block: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch swap events for a specific pair within a block range.

        Args:
            pair_address: The pool/pair address (without 0x prefix in DB)
            start_block: Starting block (inclusive)
            end_block: Ending block (inclusive)

        Returns:
            List of swap event dictionaries
        """
        # Remove 0x prefix if present for DB query
        clean_address = pair_address.lower().replace("0x", "")

        # Build query
        query = SwapEvent.filter(evt_address=clean_address)

        if start_block is not None:
            query = query.filter(evt_block_number__gte=start_block)

        if end_block is not None:
            query = query.filter(evt_block_number__lte=end_block)

        # Execute query
        events = await query.order_by("evt_block_number").values(
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

        # Rename fields to match expected interface
        return [
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

    @retry_on_db_error
    async def get_sqrt_price_at_block(
        self, pair_address: str, block_number: int
    ) -> Optional[int]:
        """
        Get the price (token1/token0) at a specific block.
        Uses the most recent swap before or at the block.
        """
        clean_address = pair_address.lower().replace("0x", "")

        result = await (
            SwapEvent.filter(
                evt_address=clean_address, evt_block_number__lte=block_number
            )
            .order_by("-evt_block_number")
            .first()
        )

        if result and result.sqrt_price_x96:
            # Convert sqrtPriceX96 to actual price
            sqrt_price = int(result.sqrt_price_x96)
            return sqrt_price
        return None

    @retry_on_db_error
    async def get_mint_events(
        self,
        pair_address: str,
        start_block: Optional[int] = None,
        end_block: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch mint (liquidity addition) events."""
        clean_address = pair_address.lower().replace("0x", "")

        # Build query
        query = MintEvent.filter(evt_address=clean_address)

        if start_block is not None:
            query = query.filter(evt_block_number__gte=start_block)

        if end_block is not None:
            query = query.filter(evt_block_number__lte=end_block)

        # Execute query
        events = await query.order_by("evt_block_number").values(
            "evt_block_number",
            "evt_tx_hash",
            "tick_lower",
            "tick_upper",
            "amount",
            "amount0",
            "amount1",
            "owner",
            "sender",
        )

        # Rename fields to match expected interface
        return [
            {
                "block_number": e["evt_block_number"],
                "transaction_hash": e["evt_tx_hash"],
                "tick_lower": e["tick_lower"],
                "tick_upper": e["tick_upper"],
                "amount": e["amount"],
                "amount0": e["amount0"],
                "amount1": e["amount1"],
                "owner": e["owner"],
                "sender": e["sender"],
            }
            for e in events
        ]

    @retry_on_db_error
    async def get_burn_events(
        self,
        pair_address: str,
        start_block: Optional[int] = None,
        end_block: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch burn (liquidity removal) events."""
        clean_address = pair_address.lower().replace("0x", "")

        # Build query
        query = BurnEvent.filter(evt_address=clean_address)

        if start_block is not None:
            query = query.filter(evt_block_number__gte=start_block)

        if end_block is not None:
            query = query.filter(evt_block_number__lte=end_block)

        # Execute query
        events = await query.order_by("evt_block_number").values(
            "evt_block_number",
            "evt_tx_hash",
            "tick_lower",
            "tick_upper",
            "amount",
            "amount0",
            "amount1",
            "owner",
        )

        # Rename fields to match expected interface
        return [
            {
                "block_number": e["evt_block_number"],
                "transaction_hash": e["evt_tx_hash"],
                "tick_lower": e["tick_lower"],
                "tick_upper": e["tick_upper"],
                "amount": e["amount"],
                "amount0": e["amount0"],
                "amount1": e["amount1"],
                "owner": e["owner"],
            }
            for e in events
        ]

    @retry_on_db_error
    async def get_collect_events(
        self,
        pair_address: str,
        start_block: Optional[int] = None,
        end_block: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch collect (fee collection) events."""
        clean_address = pair_address.lower().replace("0x", "")

        # Build query
        query = CollectEvent.filter(evt_address=clean_address)

        if start_block is not None:
            query = query.filter(evt_block_number__gte=start_block)

        if end_block is not None:
            query = query.filter(evt_block_number__lte=end_block)

        # Execute query
        events = await query.order_by("evt_block_number").values(
            "evt_block_number",
            "evt_tx_hash",
            "tick_lower",
            "tick_upper",
            "amount0",
            "amount1",
            "owner",
            "recipient",
        )

        # Rename fields to match expected interface
        return [
            {
                "block_number": e["evt_block_number"],
                "transaction_hash": e["evt_tx_hash"],
                "tick_lower": e["tick_lower"],
                "tick_upper": e["tick_upper"],
                "amount0": e["amount0"],
                "amount1": e["amount1"],
                "owner": e["owner"],
                "recipient": e["recipient"],
            }
            for e in events
        ]

    @retry_on_db_error
    async def get_fee_growth(
        self, pair_address: str, start_block: int, end_block: int
    ) -> Dict[str, float]:
        """
        Calculate fee growth between two blocks.

        Returns:
            Dictionary with 'fee0' and 'fee1' keys
        """
        clean_address = pair_address.lower().replace("0x", "")

        result = await (
            CollectEvent.filter(
                evt_address=clean_address,
                evt_block_number__gte=start_block,
                evt_block_number__lte=end_block,
            )
            .annotate(total_fee0=Sum("amount0"), total_fee1=Sum("amount1"))
            .values("total_fee0", "total_fee1")
        )

        if result:
            return {
                "fee0": float(result[0]["total_fee0"] or 0),
                "fee1": float(result[0]["total_fee1"] or 0),
            }
        return {"fee0": 0.0, "fee1": 0.0}

    @retry_on_db_error
    async def get_tick_at_block(
        self, pair_address: str, block_number: int
    ) -> Optional[int]:
        """
        Get the current tick at a specific block.
        """
        clean_address = pair_address.lower().replace("0x", "")

        result = await (
            SwapEvent.filter(
                evt_address=clean_address, evt_block_number__lte=block_number
            )
            .order_by("-evt_block_number")
            .first()
        )

        if result and result.tick is not None:
            return int(result.tick)
        return None

    @retry_on_db_error
    async def get_miner_vault_fees(
        self,
        sn_liquditiy_manager_addresses: List[str],
        start_block: int,
        end_block: int,
    ) -> Dict[str, Dict[str, float]]:
        """
        Calculate total fees collected by miner vaults in a period.
        Used for the 30% LP Alignment score.

        Returns:
            Dictionary mapping sn_liquditiy_manager_address to {'fee0': float, 'fee1': float}
        """
        # Clean addresses
        clean_addresses = [
            addr.lower().replace("0x", "") for addr in sn_liquditiy_manager_addresses
        ]

        results = await (
            CollectEvent.filter(
                owner__in=clean_addresses,
                evt_block_number__gte=start_block,
                evt_block_number__lte=end_block,
            )
            .group_by("owner")
            .annotate(total_fee0=Sum("amount0"), total_fee1=Sum("amount1"))
            .values("owner", "total_fee0", "total_fee1")
        )

        vault_fees = {}
        for row in results:
            vault_fees[row["owner"]] = {
                "fee0": float(row["total_fee0"] or 0),
                "fee1": float(row["total_fee1"] or 0),
            }

        return vault_fees

    async def test_connection(self) -> bool:
        """
        Test if the database connection is working.

        Returns:
            True if connection successful, False otherwise
        """
        try:
            # Try to count swap events to test connection
            await SwapEvent.all().count()
            return True
        except Exception as e:
            logger.error(f"Database connection test failed: {e}")
            return False


async def init_pool_events_db(db_url: str):
    """
    Initialize Tortoise ORM for pool events database.

    This should be called before using PoolDataDB.

    Args:
        db_url: Database URL in format: postgresql+asyncpg://user:pass@host:port/db

    Example:
        await init_pool_events_db("postgresql+asyncpg://user:pass@localhost:5432/pool_events")
    """
    # Convert postgresql+asyncpg to postgres for Tortoise
    if db_url.startswith("postgresql+asyncpg://"):
        db_url = db_url.replace("postgresql+asyncpg://", "postgres://")

    await Tortoise.init(
        db_url=db_url,
        modules={"pool_events": ["validator.models.pool_events"]},
    )
    logger.info("Pool events database initialized")


async def close_pool_events_db():
    """Close pool events database connections."""
    await Tortoise.close_connections()
    logger.info("Pool events database connections closed")

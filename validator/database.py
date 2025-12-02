"""
Database utilities for querying pool events from Postgres.
Adapted for the actual substreams schema with separate tables for swaps, mints, burns, etc.

Includes retry logic for transient failures.
"""
import psycopg2
from psycopg2.extras import RealDictCursor
from typing import List, Dict, Any, Optional
from abc import ABC, abstractmethod
import logging
from contextlib import contextmanager
import time

logger = logging.getLogger(__name__)


class DataSource(ABC):
    """
    Abstract base class for pool data sources.

    This allows the backtester and other components to work with
    different data sources (database, API, CSV files, etc.) without
    being tightly coupled to a specific implementation.
    """

    @abstractmethod
    def get_swap_events(
        self,
        pair_address: str,
        start_block: Optional[int] = None,
        end_block: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Fetch swap events for a specific pair within a block range."""
        pass

    @abstractmethod
    def get_price_at_block(
        self,
        pair_address: str,
        block_number: int
    ) -> Optional[float]:
        """Get the price (token1/token0) at a specific block."""
        pass

    @abstractmethod
    def get_mint_events(
        self,
        pair_address: str,
        start_block: Optional[int] = None,
        end_block: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Fetch mint (liquidity addition) events."""
        pass

    @abstractmethod
    def get_burn_events(
        self,
        pair_address: str,
        start_block: Optional[int] = None,
        end_block: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Fetch burn (liquidity removal) events."""
        pass

    @abstractmethod
    def get_collect_events(
        self,
        pair_address: str,
        start_block: Optional[int] = None,
        end_block: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Fetch collect (fee collection) events."""
        pass

    @abstractmethod
    def get_fee_growth(
        self,
        pair_address: str,
        start_block: int,
        end_block: int
    ) -> Dict[str, float]:
        """Calculate fee growth between two blocks."""
        pass

    @abstractmethod
    def get_tick_at_block(
        self,
        pair_address: str,
        block_number: int
    ) -> Optional[int]:
        """Get the current tick at a specific block."""
        pass

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAY_BASE = 1.0  # Base delay in seconds (exponential backoff)
RETRYABLE_ERRORS = (
    psycopg2.OperationalError,
    psycopg2.InterfaceError,
    ConnectionError,
    TimeoutError,
)


def retry_on_db_error(func):
    """
    Decorator that retries database operations on transient failures.
    Uses exponential backoff.
    """
    def wrapper(*args, **kwargs):
        last_exception = None
        for attempt in range(MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except RETRYABLE_ERRORS as e:
                last_exception = e
                delay = RETRY_DELAY_BASE * (2 ** attempt)
                logger.warning(
                    f"Database operation failed (attempt {attempt + 1}/{MAX_RETRIES}): {e}. "
                    f"Retrying in {delay:.1f}s..."
                )
                time.sleep(delay)
            except Exception as e:
                # Non-retryable error, raise immediately
                raise
        # All retries exhausted
        logger.error(f"Database operation failed after {MAX_RETRIES} attempts")
        raise last_exception
    return wrapper


class PoolDataDB(DataSource):
    """
    PostgreSQL implementation of the DataSource interface.

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
    - Connection pooling friendly (creates new connections per operation)
    """

    def __init__(
        self,
        host: str = None,
        port: int = None,
        database: str = None,
        user: str = None,
        password: str = None,
        connection_string: str = None
    ):
        if connection_string:
            self.connection_string = connection_string
            self.connection_params = None
        else:
            self.connection_string = None
            self.connection_params = {
                'host': host,
                'port': port,
                'database': database,
                'user': user,
                'password': password
            }

    @contextmanager
    def get_connection(self):
        """Context manager for database connections with automatic cleanup."""
        conn = None
        try:
            if self.connection_string:
                conn = psycopg2.connect(self.connection_string, connect_timeout=10)
            else:
                conn = psycopg2.connect(**self.connection_params, connect_timeout=10)
            yield conn
        except psycopg2.Error as e:
            # Sanitize error message to avoid logging credentials
            error_msg = str(e).split('@')[0] if '@' in str(e) else str(e)
            logger.error(f"Database connection error: {error_msg}")
            raise
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    @retry_on_db_error
    def get_swap_events(
        self,
        pair_address: str,
        start_block: Optional[int] = None,
        end_block: Optional[int] = None
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
        clean_address = pair_address.lower().replace('0x', '')

        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                query = """
                    SELECT
                        evt_block_number as block_number,
                        evt_tx_hash as transaction_hash,
                        evt_block_time as timestamp,
                        sqrt_price_x96,
                        tick,
                        amount0,
                        amount1,
                        liquidity,
                        sender,
                        recipient
                    FROM swaps
                    WHERE evt_address = %s
                """
                params = [clean_address]

                if start_block is not None:
                    query += " AND evt_block_number >= %s"
                    params.append(start_block)

                if end_block is not None:
                    query += " AND evt_block_number <= %s"
                    params.append(end_block)

                query += " ORDER BY evt_block_number ASC"

                cursor.execute(query, params)
                return [dict(row) for row in cursor.fetchall()]

    @retry_on_db_error
    def get_price_at_block(
        self,
        pair_address: str,
        block_number: int
    ) -> Optional[float]:
        """
        Get the price (token1/token0) at a specific block.
        Uses the most recent swap before or at the block.
        """
        clean_address = pair_address.lower().replace('0x', '')

        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                query = """
                    SELECT sqrt_price_x96
                    FROM swaps
                    WHERE evt_address = %s
                        AND evt_block_number <= %s
                    ORDER BY evt_block_number DESC
                    LIMIT 1
                """
                cursor.execute(query, [clean_address, block_number])
                result = cursor.fetchone()

                if result and result['sqrt_price_x96']:
                    # Convert sqrtPriceX96 to actual price
                    sqrt_price = int(result['sqrt_price_x96'])
                    price = (sqrt_price / (2 ** 96)) ** 2
                    return price
                return None

    @retry_on_db_error
    def get_mint_events(
        self,
        pair_address: str,
        start_block: Optional[int] = None,
        end_block: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Fetch mint (liquidity addition) events."""
        clean_address = pair_address.lower().replace('0x', '')

        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                query = """
                    SELECT
                        evt_block_number as block_number,
                        evt_tx_hash as transaction_hash,
                        tick_lower,
                        tick_upper,
                        amount,
                        amount0,
                        amount1,
                        owner,
                        sender
                    FROM mints
                    WHERE evt_address = %s
                """
                params = [clean_address]

                if start_block is not None:
                    query += " AND evt_block_number >= %s"
                    params.append(start_block)

                if end_block is not None:
                    query += " AND evt_block_number <= %s"
                    params.append(end_block)

                query += " ORDER BY evt_block_number ASC"

                cursor.execute(query, params)
                return [dict(row) for row in cursor.fetchall()]

    @retry_on_db_error
    def get_burn_events(
        self,
        pair_address: str,
        start_block: Optional[int] = None,
        end_block: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Fetch burn (liquidity removal) events."""
        clean_address = pair_address.lower().replace('0x', '')

        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                query = """
                    SELECT
                        evt_block_number as block_number,
                        evt_tx_hash as transaction_hash,
                        tick_lower,
                        tick_upper,
                        amount,
                        amount0,
                        amount1,
                        owner
                    FROM burns
                    WHERE evt_address = %s
                """
                params = [clean_address]

                if start_block is not None:
                    query += " AND evt_block_number >= %s"
                    params.append(start_block)

                if end_block is not None:
                    query += " AND evt_block_number <= %s"
                    params.append(end_block)

                query += " ORDER BY evt_block_number ASC"

                cursor.execute(query, params)
                return [dict(row) for row in cursor.fetchall()]

    @retry_on_db_error
    def get_collect_events(
        self,
        pair_address: str,
        start_block: Optional[int] = None,
        end_block: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Fetch collect (fee collection) events."""
        clean_address = pair_address.lower().replace('0x', '')

        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                query = """
                    SELECT
                        evt_block_number as block_number,
                        evt_tx_hash as transaction_hash,
                        tick_lower,
                        tick_upper,
                        amount0,
                        amount1,
                        owner,
                        recipient
                    FROM collects
                    WHERE evt_address = %s
                """
                params = [clean_address]

                if start_block is not None:
                    query += " AND evt_block_number >= %s"
                    params.append(start_block)

                if end_block is not None:
                    query += " AND evt_block_number <= %s"
                    params.append(end_block)

                query += " ORDER BY evt_block_number ASC"

                cursor.execute(query, params)
                return [dict(row) for row in cursor.fetchall()]

    @retry_on_db_error
    def get_fee_growth(
        self,
        pair_address: str,
        start_block: int,
        end_block: int
    ) -> Dict[str, float]:
        """
        Calculate fee growth between two blocks.

        Returns:
            Dictionary with 'fee0' and 'fee1' keys
        """
        clean_address = pair_address.lower().replace('0x', '')

        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                query = """
                    SELECT
                        COALESCE(SUM(amount0), 0) as total_fee0,
                        COALESCE(SUM(amount1), 0) as total_fee1
                    FROM collects
                    WHERE evt_address = %s
                        AND evt_block_number >= %s
                        AND evt_block_number <= %s
                """
                cursor.execute(query, [clean_address, start_block, end_block])
                result = cursor.fetchone()

                return {
                    'fee0': float(result['total_fee0'] or 0),
                    'fee1': float(result['total_fee1'] or 0)
                }

    @retry_on_db_error
    def get_tick_at_block(
        self,
        pair_address: str,
        block_number: int
    ) -> Optional[int]:
        """
        Get the current tick at a specific block.
        """
        clean_address = pair_address.lower().replace('0x', '')

        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                query = """
                    SELECT tick
                    FROM swaps
                    WHERE evt_address = %s
                        AND evt_block_number <= %s
                    ORDER BY evt_block_number DESC
                    LIMIT 1
                """
                cursor.execute(query, [clean_address, block_number])
                result = cursor.fetchone()

                if result and result['tick'] is not None:
                    return int(result['tick'])
                return None

    @retry_on_db_error
    def get_miner_vault_fees(
        self,
        vault_addresses: List[str],
        start_block: int,
        end_block: int
    ) -> Dict[str, Dict[str, float]]:
        """
        Calculate total fees collected by miner vaults in a period.
        Used for the 30% LP Alignment score.

        Returns:
            Dictionary mapping vault_address to {'fee0': float, 'fee1': float}
        """
        # Clean addresses
        clean_addresses = [addr.lower().replace('0x', '') for addr in vault_addresses]

        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                query = """
                    SELECT
                        owner,
                        COALESCE(SUM(amount0), 0) as total_fee0,
                        COALESCE(SUM(amount1), 0) as total_fee1
                    FROM collects
                    WHERE owner = ANY(%s)
                        AND evt_block_number >= %s
                        AND evt_block_number <= %s
                    GROUP BY owner
                """
                cursor.execute(query, [clean_addresses, start_block, end_block])
                results = cursor.fetchall()

                vault_fees = {}
                for row in results:
                    vault_fees[row['owner']] = {
                        'fee0': float(row['total_fee0'] or 0),
                        'fee1': float(row['total_fee1'] or 0)
                    }

                return vault_fees

    def test_connection(self) -> bool:
        """
        Test if the database connection is working.

        Returns:
            True if connection successful, False otherwise
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    return True
        except Exception as e:
            logger.error(f"Database connection test failed: {e}")
            return False

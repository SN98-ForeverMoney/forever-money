"""
Miner implementation for SN98 ForeverMoney using Bittensor axon.

This miner serves LP strategy requests from validators using Bittensor's
dendrite/axon communication protocol.

Usage:
    python -m miner.miner --wallet.name <wallet_name> --wallet.hotkey <hotkey_name>
"""
import os
import logging
import argparse
from typing import Optional, Tuple
import bittensor as bt
from dotenv import load_dotenv

from protocol import StrategyRequest, RebalanceQuery, Strategy
from miner.models import MinerMetadata
from validator.database import PoolDataDB
from miner.strategy import SimpleStrategyGenerator

# Load environment
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
MINER_VERSION = os.getenv('MINER_VERSION', '1.0.0-mvp')
MODEL_INFO = os.getenv('MODEL_INFO', 'simple-rule-based')


class SN98Miner:
    """
    SN98 ForeverMoney Miner using Bittensor axon.

    Serves two endpoints:
    1. StrategyRequest - Generate LP strategies
    2. RebalanceQuery - Dynamic rebalancing decisions
    """

    def __init__(
        self,
        wallet: bt.wallet,
        subtensor: bt.subtensor,
        config: bt.config,
    ):
        """
        Initialize miner.

        Args:
            wallet: Bittensor wallet for authentication
            subtensor: Bittensor subtensor connection
            config: Configuration object
        """
        self.wallet = wallet
        self.subtensor = subtensor
        self.config = config

        logger.info(f"Starting SN98 Miner v{MINER_VERSION}")
        logger.info(f"Wallet: {wallet.hotkey.ss58_address}")
        logger.info(f"Model: {MODEL_INFO}")

        # Initialize database connection
        self.db_connection: Optional[PoolDataDB] = None
        db_connection_string = os.getenv('DB_CONNECTION_STRING')
        if db_connection_string:
            try:
                self.db_connection = PoolDataDB(connection_string=db_connection_string)
                logger.info("Database connection initialized")
            except Exception as e:
                logger.warning(f"Could not initialize database: {e}")

        # Initialize strategy generator
        self.strategy_generator = SimpleStrategyGenerator(db=self.db_connection)

        # Create and configure axon
        self.axon = bt.axon(wallet=wallet, config=config)

        # Attach forward functions to axon
        self.axon.attach(
            forward_fn=self.strategy_request_handler,
            blacklist_fn=self.blacklist_strategy_request,
            priority_fn=self.priority_strategy_request,
        )

        self.axon.attach(
            forward_fn=self.rebalance_query_handler,
            blacklist_fn=self.blacklist_rebalance_query,
            priority_fn=self.priority_rebalance_query,
        )

        logger.info(f"Axon created on port {self.axon.port}")

    async def strategy_request_handler(self, synapse: StrategyRequest) -> StrategyRequest:
        """
        Handle StrategyRequest synapse from validators.

        This replaces the /predict_strategy HTTP endpoint.

        Args:
            synapse: StrategyRequest synapse with request data

        Returns:
            StrategyRequest synapse with response data populated
        """
        try:
            logger.info(
                f"Received strategy request for pair {synapse.pair_address}, "
                f"block {synapse.target_block}"
            )

            # Generate strategy using the existing strategy generator
            strategy = self.strategy_generator.generate_strategy_from_synapse(synapse)

            # Populate response fields in synapse
            synapse.strategy = strategy
            synapse.miner_metadata = MinerMetadata(
                version=MINER_VERSION,
                model_info=MODEL_INFO
            )

            logger.info(
                f"Generated strategy with {len(strategy.positions)} positions"
            )

            return synapse

        except Exception as e:
            logger.error(f"Error processing strategy request: {e}", exc_info=True)
            # Return synapse with None values to indicate error
            synapse.strategy = None
            synapse.miner_metadata = None
            return synapse

    async def rebalance_query_handler(self, synapse: RebalanceQuery) -> RebalanceQuery:
        """
        Handle RebalanceQuery synapse from validators.

        This replaces the /should_rebalance HTTP endpoint.

        Args:
            synapse: RebalanceQuery synapse with query data

        Returns:
            RebalanceQuery synapse with response data populated
        """
        try:
            logger.debug(
                f"Rebalance check for block {synapse.block_number}, "
                f"price {synapse.current_price:.2f}"
            )

            # Determine if we should rebalance
            should_rebal, new_positions, reason = (
                self.strategy_generator.should_rebalance_from_synapse(synapse)
            )

            # Populate response fields in synapse
            synapse.rebalance = should_rebal
            synapse.new_positions = new_positions if should_rebal else None
            synapse.reason = reason

            return synapse

        except Exception as e:
            logger.error(f"Error processing rebalance query: {e}", exc_info=True)
            # Return synapse with error indication
            synapse.rebalance = False
            synapse.new_positions = None
            synapse.reason = f"Error: {str(e)}"
            return synapse

    def blacklist_strategy_request(self, synapse: StrategyRequest) -> Tuple[bool, str]:
        """
        Blacklist function for StrategyRequest.

        Can be used to filter requests based on hotkey, stake, etc.

        Args:
            synapse: StrategyRequest synapse

        Returns:
            Tuple of (is_blacklisted, reason)
        """
        # Accept all requests for now
        # In production, you might want to:
        # - Check validator stake
        # - Rate limit by hotkey
        # - Verify signature
        return False, ""

    def priority_strategy_request(self, synapse: StrategyRequest) -> float:
        """
        Priority function for StrategyRequest.

        Higher priority requests are processed first.

        Args:
            synapse: StrategyRequest synapse

        Returns:
            Priority score (higher = more priority)
        """
        # Equal priority for all requests for now
        # In production, you might prioritize by:
        # - Validator stake
        # - Historical payment
        # - Request complexity
        return 0.0

    def blacklist_rebalance_query(self, synapse: RebalanceQuery) -> Tuple[bool, str]:
        """
        Blacklist function for RebalanceQuery.

        Args:
            synapse: RebalanceQuery synapse

        Returns:
            Tuple of (is_blacklisted, reason)
        """
        # Accept all requests
        return False, ""

    def priority_rebalance_query(self, synapse: RebalanceQuery) -> float:
        """
        Priority function for RebalanceQuery.

        Args:
            synapse: RebalanceQuery synapse

        Returns:
            Priority score
        """
        # Equal priority for all requests
        return 0.0

    def run(self):
        """
        Start the miner axon server.

        This method blocks until the miner is stopped.
        """
        logger.info("Starting axon server...")

        # Start the axon
        self.axon.start()

        # Serve the axon
        try:
            logger.info(f"Miner serving on {self.axon.ip}:{self.axon.port}")
            logger.info("Press Ctrl+C to stop")

            # Keep the miner running
            self.axon.serve(
                subtensor=self.subtensor,
                netuid=self.config.netuid,
            )

            # This blocks until interrupted
            bt.logging.info("Miner is running. Press Ctrl+C to stop.")
            import time
            while True:
                time.sleep(1)

        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received, stopping miner...")
            self.stop()

    def stop(self):
        """Stop the miner axon server."""
        logger.info("Stopping axon...")
        self.axon.stop()
        logger.info("Miner stopped")


def get_config():
    """
    Create and return configuration for the miner.

    Returns:
        bt.config object with all necessary configuration
    """
    parser = argparse.ArgumentParser(description="SN98 ForeverMoney Miner")

    # Add wallet arguments
    parser.add_argument(
        "--wallet.name",
        type=str,
        default="default",
        help="Name of wallet"
    )
    parser.add_argument(
        "--wallet.hotkey",
        type=str,
        default="default",
        help="Hotkey name"
    )

    # Add subtensor arguments
    parser.add_argument(
        "--subtensor.network",
        type=str,
        default="finney",
        help="Bittensor network (finney, test, local)"
    )
    parser.add_argument(
        "--subtensor.chain_endpoint",
        type=str,
        default=None,
        help="Chain endpoint override"
    )

    # Add netuid
    parser.add_argument(
        "--netuid",
        type=int,
        default=98,
        help="Network UID for SN98"
    )

    # Add axon arguments
    parser.add_argument(
        "--axon.port",
        type=int,
        default=None,
        help="Port for axon server"
    )
    parser.add_argument(
        "--axon.ip",
        type=str,
        default="0.0.0.0",
        help="IP address for axon server"
    )
    parser.add_argument(
        "--axon.external_ip",
        type=str,
        default=None,
        help="External IP address for axon"
    )

    # Add logging
    parser.add_argument(
        "--logging.debug",
        action="store_true",
        default=False,
        help="Enable debug logging"
    )
    parser.add_argument(
        "--logging.trace",
        action="store_true",
        default=False,
        help="Enable trace logging"
    )

    # Parse config
    config = bt.config(parser)

    # Set up logging
    if config.logging.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    if config.logging.trace:
        bt.logging.set_trace(True)

    return config


def main():
    """
    Main entry point for the miner.

    Usage:
        python -m miner.miner --wallet.name <wallet_name> --wallet.hotkey <hotkey_name>
    """
    # Get configuration
    config = get_config()

    logger.info(f"Config: {config}")

    # Create wallet
    wallet = bt.wallet(config=config)
    logger.info(f"Wallet: {wallet}")

    # Create subtensor
    subtensor = bt.subtensor(config=config)
    logger.info(f"Subtensor: {subtensor}")

    # Create and run miner
    miner = SN98Miner(
        wallet=wallet,
        subtensor=subtensor,
        config=config,
    )

    miner.run()


if __name__ == '__main__':
    main()

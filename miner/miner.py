"""
Miner implementation for SN98 ForeverMoney using Bittensor axon.

Uses rebalance-only protocol:
- No StrategyRequest (removed)
- Only RebalanceQuery for dynamic rebalancing decisions

Usage:
    python -m miner.miner --wallet.name <wallet_name> --wallet.hotkey <hotkey_name>
"""
import logging
import argparse
import time
from typing import Optional, Tuple, Any
import bittensor as bt

from protocol.synapses import RebalanceQuery
from validator.utils.env import MINER_VERSION, NETUID, SUBTENSOR_NETWORK

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class SN98Miner:
    """
    SN98 ForeverMoney Miner using rebalance-only protocol.

    Serves one endpoint:
    - RebalanceQuery: Dynamic rebalancing decisions during backtesting
    """

    def __init__(
        self,
        wallet: Any,  # bt.Wallet
        subtensor: Any,  # bt.Subtensor
        config: Any,  # bt.Config
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

        # Create and configure axon
        self.axon = bt.Axon(wallet=wallet, config=config)

        # Attach only RebalanceQuery handler
        self.axon.attach(
            forward_fn=self.rebalance_query_handler,
            blacklist_fn=self.blacklist_rebalance_query,
            priority_fn=self.priority_rebalance_query,
        )

        logger.info(f"Axon created on port {self.axon.port}")
        logger.info(f"Serving RebalanceQuery endpoint only")

    async def rebalance_query_handler(self, synapse: RebalanceQuery) -> RebalanceQuery:
        """
        Handle RebalanceQuery synapse from validators.

        The validator is running a backtest simulation and asking:
        "At this block, with these positions, should I rebalance?"

        Miners can:
        1. Refuse the job entirely (accepted=False)
        2. Keep current positions (new_positions=current_positions)
        3. Rebalance to new positions (new_positions=[...])

        Args:
            synapse: RebalanceQuery synapse with simulation state

        Returns:
            RebalanceQuery synapse with response populated
        """
        pass

    def _should_accept_job(self, synapse: RebalanceQuery) -> Tuple[bool, Optional[str]]:
        """
        Determine if miner should accept this job.

        Override this method to implement custom job filtering logic.

        Args:
            synapse: RebalanceQuery synapse

        Returns:
            Tuple of (should_accept, refusal_reason)
        """
        # By default, accept all jobs
        # You can implement custom filtering logic here:
        #
        # Example 1: Only work on specific pairs
        # accepted_pairs = ['0x123...', '0x456...']
        # if synapse.pair_address not in accepted_pairs:
        #     return False, "Only working on whitelisted pairs"
        #
        # Example 2: Only work on evaluation rounds
        # if synapse.round_type == 'live':
        #     return False, "Not participating in live rounds yet"
        #
        # Example 3: Check rebalance frequency (avoid spam)
        # if synapse.rebalances_so_far >= 10:
        #     return False, "Max rebalances reached"

        return True, None

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

    # Wallet arguments
    parser.add_argument("--wallet.name", type=str, required=True, help="Wallet name")
    parser.add_argument(
        "--wallet.hotkey", type=str, required=True, help="Wallet hotkey"
    )

    # Network arguments
    parser.add_argument(
        "--subtensor.network",
        type=str,
        default=None,
        help=f"Subtensor network endpoint (e.g., ws://127.0.0.1:9944, wss://entrypoint-finney.opentensor.ai:443, or finney/test/local). Default: {SUBTENSOR_NETWORK}",
    )
    parser.add_argument(
        "--netuid",
        type=int,
        default=None,
        help=f"Network UID. Default: {NETUID}",
    )

    # Parse config with bt.config to get bittensor defaults
    config = bt.config(parser)

    # Override with CLI args or environment variables
    # Priority: CLI args > env vars > defaults
    if hasattr(config, 'subtensor') and hasattr(config, 'subtensor.network'):
        # CLI arg provided
        pass
    elif SUBTENSOR_NETWORK:
        # Use env var
        config.subtensor.network = SUBTENSOR_NETWORK

    if hasattr(config, 'netuid') and config.netuid is not None:
        # CLI arg provided
        pass
    elif NETUID:
        # Use env var
        config.netuid = NETUID

    return config


def main():
    """
    Main entry point for the miner.

    Usage:
        python -m miner.miner --wallet.name <wallet> --wallet.hotkey <hotkey>

    All other configuration loaded from .env file.
    """
    # Get configuration
    config = get_config()

    logger.info(f"Config: {config}")

    # Create wallet
    wallet = bt.wallet(config=config)
    logger.info(f"Wallet: {wallet}")

    # Create subtensor
    subtensor = bt.Subtensor(config=config)
    logger.info(f"Subtensor: {subtensor}")

    # Create miner
    miner = SN98Miner(
        wallet=wallet,
        subtensor=subtensor,
        config=config,
    )

    # Run miner (synchronous Bittensor serving)
    miner.run()

if __name__ == "__main__":
    main()

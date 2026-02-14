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


import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


from protocol.synapses import RebalanceQuery
from protocol.models import Position
from validator.utils.env import MINER_VERSION, NETUID, SUBTENSOR_NETWORK, BT_WALLET_PATH
from validator.utils.math import UniswapV3Math

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
        """

        logger.info(f"Received RebalanceQuery: block={synapse.block_number}, rebalances={synapse.rebalances_so_far}")
        
        # Check if we should accept
        should_accept, refusal_reason = self._should_accept_job(synapse)
        if not should_accept:
            synapse.accepted = False
            synapse.refusal_reason = refusal_reason
            return synapse

        synapse.accepted = True
        
        # Simple Logic:
        # 1. If we have no positions, deploy a range.
        # 2. If we have positions, check if price is near the edge.
        # 3. If price is near edge, rebalance.
        
        current_tick = UniswapV3Math.get_tick_from_sqrt_price_x96(synapse.current_price)
        
        should_rebalance = False
        if not synapse.current_positions:
            should_rebalance = True
            logger.info("No current positions. Rebalancing initialized.")
        else:
            # Check existing positions
            # Assuming we manage one main position for simplicity
            pos = synapse.current_positions[0]
            tick_width = pos.tick_upper - pos.tick_lower
            buffer = tick_width * 0.2 # Rebalance if within 20% of edge
            
            if current_tick < pos.tick_lower + buffer or current_tick > pos.tick_upper - buffer:
                should_rebalance = True
                logger.info(f"Price (tick {current_tick}) near edge of [{pos.tick_lower}, {pos.tick_upper}]. Rebalancing.")

        if should_rebalance:
            # Create new position centered on current tick
            width = 2000  # Configurable width
            tick_spacing = synapse.tick_spacing  # From validator via liq_manager.get_tick_spacing()

            # Snap to tick spacing (ticks must be multiples of spacing)
            center_tick = (current_tick // tick_spacing) * tick_spacing
            lower_tick = (center_tick - width) // tick_spacing * tick_spacing
            upper_tick = (center_tick + width) // tick_spacing * tick_spacing
            
            # Allocate all available inventory
            # Note: validator will calculate actual amounts used based on price
            new_pos = Position(
                tick_lower=lower_tick,
                tick_upper=upper_tick,
                allocation0=synapse.inventory_remaining["amount0"],
                allocation1=synapse.inventory_remaining["amount1"]
            )
            
            synapse.desired_positions = [new_pos]
            logger.info(f"Proposing new position: [{lower_tick}, {upper_tick}]")
        else:
            # Keep current positions: return current_positions instead of None
            synapse.desired_positions = list(synapse.current_positions)
            logger.info("Keeping current positions.")

        return synapse

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
    parser.add_argument(
        "--wallet.path", type=str, default=BT_WALLET_PATH, help="Wallet directory (default: BT_WALLET_PATH env or ~/.bittensor/wallets)"
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

    bt.Axon.add_args(parser)

    # Parse config with bt.Config to get bittensor defaults
    config = bt.Config(parser)

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
    wallet = bt.Wallet(config=config)
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

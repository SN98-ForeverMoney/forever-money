"""
Main entry point for SN98 Validator.
"""
import os
import sys
import logging
import argparse
from dotenv import load_dotenv

import bittensor as bt

from validator.validator import SN98Validator
from validator.database import PoolDataDB
from protocol import Inventory

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('validator.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


def get_config():
    """Load configuration from environment and arguments."""
    load_dotenv()

    parser = argparse.ArgumentParser(description='SN98 ForeverMoney Validator')

    # Bittensor arguments
    parser.add_argument('--netuid', type=int, default=98, help='Subnet UID')
    parser.add_argument('--subtensor.network', type=str, default='finney', help='Subtensor network')
    parser.add_argument('--wallet.name', type=str, default='default', help='Wallet name')
    parser.add_argument('--wallet.hotkey', type=str, default='default', help='Wallet hotkey')

    # SN98 specific arguments
    parser.add_argument('--pair_address', type=str, help='Trading pair address')
    parser.add_argument('--target_block', type=int, help='Target block for round')
    parser.add_argument('--start_block', type=int, help='Starting block for backtest')
    parser.add_argument('--miner-uids', type=str, help='Comma-separated list of miner UIDs to query (e.g., "0,1,2"). If not specified, queries all active miners.')
    parser.add_argument('--dry-run', action='store_true', help='Run without publishing weights to the network')

    args = parser.parse_args()

    # Parse miner UIDs if provided
    miner_uids = None
    if getattr(args, 'miner_uids', None):
        try:
            miner_uids = [int(uid.strip()) for uid in args.miner_uids.split(',')]
        except ValueError:
            logger.error(f"Invalid miner UIDs format: {args.miner_uids}. Expected comma-separated integers.")
            sys.exit(1)

    config = {
        'netuid': args.netuid,
        'subtensor_network': getattr(args, 'subtensor.network'),
        'wallet_name': getattr(args, 'wallet.name'),
        'wallet_hotkey': getattr(args, 'wallet.hotkey'),
        'pair_address': args.pair_address or os.getenv('PAIR_ADDRESS'),
        'chain_id': int(os.getenv('CHAIN_ID', 8453)),
        'target_block': args.target_block,
        'start_block': args.start_block,
        'max_il': float(os.getenv('MAX_IL', 0.10)),
        'min_tick_width': int(os.getenv('MIN_TICK_WIDTH', 60)),
        'max_rebalances': int(os.getenv('MAX_REBALANCES', 4)),
        'performance_weight': float(os.getenv('PERFORMANCE_WEIGHT', 0.7)),
        'lp_alignment_weight': float(os.getenv('LP_ALIGNMENT_WEIGHT', 0.3)),
        'top_n_strategies': int(os.getenv('TOP_N_STRATEGIES', 3)),
        'winning_strategy_file': os.getenv('WINNING_STRATEGY_FILE', 'winning_strategy.json'),
        'miner_uids': miner_uids,
        'dry_run': getattr(args, 'dry_run', False),
        'postgres_access': {
            'host': os.getenv('POSTGRES_HOST', 'localhost'),
            'port': int(os.getenv('POSTGRES_PORT', 5432)),
            'database': os.getenv('POSTGRES_DB', 'sn98_pool_data'),
            'user': os.getenv('POSTGRES_USER', 'readonly_user'),
            'password': os.getenv('POSTGRES_PASSWORD', '')
        }
    }

    return config


def main():
    """Main validator loop."""
    logger.info("Starting SN98 ForeverMoney Validator")

    # Load configuration
    config = get_config()

    # Initialize Bittensor components
    wallet = bt.wallet(name=config['wallet_name'], hotkey=config['wallet_hotkey'])
    subtensor = bt.subtensor(network=config['subtensor_network'])
    metagraph = subtensor.metagraph(netuid=config['netuid'])

    logger.info(f"Wallet: {wallet.hotkey.ss58_address}")
    logger.info(f"Network: {config['subtensor_network']}")
    logger.info(f"Netuid: {config['netuid']}")

    # Initialize database connection
    db_connection_string = os.getenv('DB_CONNECTION_STRING')
    if db_connection_string:
        logger.info("Using DB_CONNECTION_STRING for database connection")
        db = PoolDataDB(connection_string=db_connection_string)
    else:
        db = PoolDataDB(
            host=config['postgres_access']['host'],
            port=config['postgres_access']['port'],
            database=config['postgres_access']['database'],
            user=config['postgres_access']['user'],
            password=config['postgres_access']['password']
        )

    # Initialize validator
    validator = SN98Validator(
        wallet=wallet,
        subtensor=subtensor,
        metagraph=metagraph,
        db=db,
        config=config
    )

    # For MVP, run a single round
    # In production, this would run continuously
    if config['target_block'] and config['start_block']:
        # Example inventory (would come from vault state)
        inventory = Inventory(
            amount0="1000000000000000000",  # 1 ETH
            amount1="2500000000"  # 2500 USDC (6 decimals)
        )

        validator.run_round(
            pair_address=config['pair_address'],
            target_block=config['target_block'],
            inventory=inventory,
            start_block=config['start_block']
        )
    else:
        logger.error("Must provide --target_block and --start_block arguments")
        sys.exit(1)

    logger.info("Validator finished")


if __name__ == '__main__':
    main()

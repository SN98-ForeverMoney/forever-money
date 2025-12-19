"""
Main entry point for SN98 Validator (Jobs-Based Architecture).

Supports:
- Multiple concurrent jobs
- Dual-mode operation (evaluation + live)
- Reputation-based scoring
- Miner activity tracking
- Async/await with Tortoise ORM
- Rebalance-only protocol
"""
import argparse
import asyncio
import logging
import os
import sys

import bittensor as bt
from dotenv import load_dotenv
from web3 import AsyncHTTPProvider, AsyncWeb3

from validator.job_manager_async import AsyncJobManager
from validator.models_orm import init_db, close_db
from validator.round_orchestrator_async import AsyncRoundOrchestrator

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("validator.log"), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def get_config():
    """Load configuration from environment and arguments."""
    load_dotenv()

    parser = argparse.ArgumentParser(description="SN98 ForeverMoney Validator")

    # Only essential arguments (wallet credentials)
    parser.add_argument("--wallet.name", type=str, required=True, help="Wallet name")
    parser.add_argument(
        "--wallet.hotkey", type=str, required=True, help="Wallet hotkey"
    )

    args = parser.parse_args()

    # All other config from environment
    config = {
        "netuid": int(os.getenv("NETUID", 98)),
        "subtensor_network": os.getenv("SUBTENSOR_NETWORK", "finney"),
        "wallet_name": getattr(args, "wallet.name"),
        "wallet_hotkey": getattr(args, "wallet.hotkey"),
        "chain_id": int(os.getenv("CHAIN_ID", 8453)),
        "executor_bot_url": os.getenv("EXECUTOR_BOT_URL"),
        "executor_bot_api_key": os.getenv("EXECUTOR_BOT_API_KEY"),
        "rebalance_check_interval": int(os.getenv("REBALANCE_CHECK_INTERVAL", 100)),
    }

    # Build Tortoise DB URL from environment
    db_host = os.getenv("JOBS_POSTGRES_HOST", "localhost")
    db_port = int(os.getenv("JOBS_POSTGRES_PORT", 5432))
    db_name = os.getenv("JOBS_POSTGRES_DB", "sn98_jobs")
    db_user = os.getenv("JOBS_POSTGRES_USER", "sn98_user")
    db_pass = os.getenv("JOBS_POSTGRES_PASSWORD", "")

    config[
        "tortoise_db_url"
    ] = f"postgres://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"

    return config


async def run_jobs_validator(config):
    """
    Run validator in jobs-based mode with concurrent job execution.

    Uses async/await with Tortoise ORM and rebalance-only protocol.

    Args:
        config: Configuration dictionary
    """
    logger.info("=" * 80)
    logger.info("STARTING SN98 VALIDATOR (ASYNC JOBS-BASED ARCHITECTURE)")
    logger.info("=" * 80)

    # Initialize Bittensor components
    wallet = bt.Wallet(name=config["wallet_name"], hotkey=config["wallet_hotkey"])
    subtensor = bt.subtensor(network=config["subtensor_network"])
    metagraph = subtensor.metagraph(netuid=config["netuid"])
    dendrite = bt.Dendrite(wallet=wallet)

    logger.info(f"Wallet: {wallet.hotkey.ss58_address}")
    logger.info(f"Network: {config['subtensor_network']}")
    logger.info(f"Netuid: {config['netuid']}")
    logger.info(f"Protocol: Rebalance-only (no StrategyRequest)")

    # Initialize Tortoise ORM
    logger.info("Initializing Tortoise ORM...")
    await init_db(config["tortoise_db_url"])
    logger.info("Database connected")

    # Initialize Web3 for block fetching
    rpc_url = os.getenv("RPC_URL", "https://mainnet.base.org")
    w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))

    # Initialize async job manager
    job_manager = AsyncJobManager()
    logger.info("Async job manager initialized")

    # Initialize async round orchestrator
    orchestrator = AsyncRoundOrchestrator(
        job_manager=job_manager,
        dendrite=dendrite,
        metagraph=metagraph,
        config=config,
        w3=w3,
    )
    logger.info("Async round orchestrator initialized")

    # Track running jobs and their tasks
    running_jobs = {}  # job_id -> task

    logger.info("=" * 80)
    logger.info("Starting continuous job execution with dynamic job discovery...")
    logger.info("=" * 80)

    async def monitor_and_run_jobs():
        """Continuously monitor for new jobs and start them."""
        check_interval = 60  # Check for new jobs every 60 seconds

        while True:
            try:
                # Get all active jobs from database
                active_jobs = await job_manager.get_active_jobs()

                if not active_jobs:
                    logger.warning(
                        "No active jobs found. Waiting for jobs to be added..."
                    )
                    await asyncio.sleep(check_interval)
                    continue

                # Check for new jobs
                for job in active_jobs:
                    if job.job_id not in running_jobs:
                        logger.info(
                            f"NEW JOB DETECTED: {job.job_id} | "
                            f"Vault: {job.sn_liquditiy_manager_address} | "
                            f"Pair: {job.pair_address} | "
                            f"Round Duration: {job.round_duration_seconds}s"
                        )

                        # Start new task for this job
                        task = asyncio.create_task(
                            orchestrator.run_job_continuously(job),
                            name=f"job_{job.job_id}",
                        )
                        running_jobs[job.job_id] = task

                        logger.info(f"Started orchestration for job {job.job_id}")

                # Check for inactive jobs (jobs that were removed or deactivated)
                current_job_ids = {job.job_id for job in active_jobs}
                removed_jobs = set(running_jobs.keys()) - current_job_ids

                for job_id in removed_jobs:
                    logger.info(f"Job {job_id} is no longer active, cancelling task")
                    running_jobs[job_id].cancel()
                    del running_jobs[job_id]

                # Log status
                logger.info(
                    f"Currently running {len(running_jobs)} jobs: {list(running_jobs.keys())}"
                )

                await asyncio.sleep(check_interval)

            except Exception as e:
                logger.error(f"Error in job monitor: {e}", exc_info=True)
                await asyncio.sleep(check_interval)

    try:
        # Run the job monitor
        await monitor_and_run_jobs()

    except KeyboardInterrupt:
        logger.info("\n" + "=" * 80)
        logger.info("Keyboard interrupt received. Shutting down validator...")
        logger.info("=" * 80)

    finally:
        # Cancel all running job tasks
        logger.info(f"Cancelling {len(running_jobs)} running job tasks...")
        for job_id, task in running_jobs.items():
            logger.info(f"Cancelling task for job {job_id}")
            task.cancel()

        # Wait for all tasks to be cancelled
        if running_jobs:
            await asyncio.gather(*running_jobs.values(), return_exceptions=True)

        # Cleanup Tortoise ORM
        await close_db()
        logger.info("Database connections closed")


def main():
    """Main validator entry point."""
    try:
        config = get_config()
        asyncio.run(run_jobs_validator(config))
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

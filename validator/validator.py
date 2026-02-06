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

# Ensure project root is in path when run as: python validator/validator.py
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import bittensor as bt

from validator.repositories.job import JobRepository
from validator.repositories.pool import PoolDataDB
from validator.models.job import init_db, close_db
from validator.round_orchestrator import AsyncRoundOrchestrator
from validator.services.emissions import EmissionsService
from validator.services.revenue import RevenueService
from validator.utils.env import (
    NETUID,
    SUBTENSOR_NETWORK,
    EXECUTOR_BOT_URL,
    EXECUTOR_BOT_API_KEY,
    REBALANCE_CHECK_INTERVAL,
    JOBS_POSTGRES_HOST,
    JOBS_POSTGRES_PORT,
    JOBS_POSTGRES_DB,
    JOBS_POSTGRES_USER,
    JOBS_POSTGRES_PASSWORD,
    BT_WALLET_PATH
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("validator.log"), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def _is_set(value) -> bool:
    """Return True if value is set and usable (not None, 'None', or empty string)."""
    if value is None:
        return False
    s = str(value).strip()
    return s not in ("", "None", "none")


def validate_config(config: dict) -> None:
    """
    Validate required config after assembly. Exit with code 1 if any check fails.
    Focus on DB and executor bot - the main external dependencies.
    """
    errors: list[str] = []

    # Database: tortoise_db_url must be valid
    db_url = config.get("tortoise_db_url") or ""
    if not _is_set(db_url):
        errors.append("Database URL not configured (JOBS_POSTGRES_* env vars)")
    elif "postgres://" not in db_url and "postgresql://" not in db_url:
        errors.append("Database URL must be postgres:// or postgresql://")

    # Executor bot (required for live rounds)
    url = config.get("executor_bot_url")
    if not _is_set(url):
        errors.append("EXECUTOR_BOT_URL must be set (e.g. http://localhost:8000)")
    elif not (str(url).startswith("http://") or str(url).startswith("https://")):
        errors.append("EXECUTOR_BOT_URL must start with http:// or https://")

    if not _is_set(config.get("executor_bot_api_key")):
        errors.append("EXECUTOR_BOT_API_KEY must be set")

    if errors:
        logger.error("Config validation failed:")
        for e in errors:
            logger.error(f"  - {e}")
        logger.error("Please set required environment variables and restart.")
        sys.exit(1)


def get_config():
    """Load configuration from environment and arguments."""
    parser = argparse.ArgumentParser(description="SN98 ForeverMoney Validator")

    # Wallet arguments
    parser.add_argument("--wallet.name", type=str, required=True, help="Wallet name")
    parser.add_argument(
        "--wallet.hotkey", type=str, required=True, help="Wallet hotkey"
    )
    parser.add_argument(
        "--wallet.path",
        type=str,
        default=BT_WALLET_PATH,
        help="Wallet directory (default: BT_WALLET_PATH env or ~/.bittensor/wallets)",
    )

    # Network arguments
    parser.add_argument(
        "--subtensor.network",
        type=str,
        default=SUBTENSOR_NETWORK,
        help=f"Subtensor network endpoint (e.g., ws://127.0.0.1:9944, wss://entrypoint-finney.opentensor.ai:443, or finney/test/local). Default: {SUBTENSOR_NETWORK}",
    )
    parser.add_argument(
        "--netuid",
        type=int,
        default=NETUID,
        help=f"Network UID. Default: {NETUID}",
    )

    args = parser.parse_args()

    # All other config from environment, with CLI overrides
    config = {
        "netuid": args.netuid if args.netuid is not None else NETUID,
        "subtensor_network": getattr(args, "subtensor.network") or SUBTENSOR_NETWORK,
        "wallet_name": getattr(args, "wallet.name"),
        "wallet_hotkey": getattr(args, "wallet.hotkey"),
        "wallet_path": getattr(args, "wallet.path"),
        "executor_bot_url": EXECUTOR_BOT_URL,
        "executor_bot_api_key": EXECUTOR_BOT_API_KEY,
        "rebalance_check_interval": REBALANCE_CHECK_INTERVAL,
    }

    # Build Tortoise DB URL from environment
    config[
        "tortoise_db_url"
    ] = f"postgres://{JOBS_POSTGRES_USER}:{JOBS_POSTGRES_PASSWORD}@{JOBS_POSTGRES_HOST}:{JOBS_POSTGRES_PORT}/{JOBS_POSTGRES_DB}"

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
    wallet_kwargs = {"name": config["wallet_name"], "hotkey": config["wallet_hotkey"]}
    if config.get("wallet_path"):
        wallet_kwargs["path"] = config["wallet_path"]
    wallet = bt.Wallet(**wallet_kwargs)
    subtensor = bt.Subtensor(network=config["subtensor_network"])
    metagraph = subtensor.metagraph(netuid=config["netuid"])
    dendrite = bt.Dendrite(wallet=wallet)

    # Find validator's own UID (exclude from miner queries to avoid self-query)
    my_hotkey = wallet.hotkey.ss58_address
    my_uid = None
    for uid in range(len(metagraph.hotkeys)):
        if metagraph.hotkeys[uid] == my_hotkey:
            my_uid = uid
            break
    config["my_uid"] = my_uid

    logger.info(f"Wallet: {wallet.hotkey.ss58_address}")
    logger.info(f"Network: {config['subtensor_network']}")
    logger.info(f"Netuid: {config['netuid']}")
    logger.info(f"Protocol: Rebalance-only (no StrategyRequest)")

    # Initialize Tortoise ORM
    logger.info("Initializing Tortoise ORM...")
    await init_db(config["tortoise_db_url"])
    logger.info("Database connected")

    # Initialize async job manager
    job_repository = JobRepository()
    logger.info("Async job manager initialized")

    # Initialize async round orchestrator
    orchestrator = AsyncRoundOrchestrator(
        job_repository=job_repository,
        dendrite=dendrite,
        metagraph=metagraph,
        config=config,
    )
    logger.info("Async round orchestrator initialized")

    # Initialize pool data DB and revenue service (for emissions Taoflow optimization)
    pool_data_db = PoolDataDB()
    revenue_service = RevenueService(
        job_repository=job_repository,
        pool_data_db=pool_data_db,
    )
    logger.info("Revenue service initialized")


    # Initialize emissions service
    emissions_service = EmissionsService(
        metagraph=metagraph,
        subtensor=subtensor,
        job_repository=job_repository,
        netuid=config["netuid"],
        revenue_service=revenue_service,
    )
    logger.info("Emissions service initialized")

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
                active_jobs = await job_repository.get_active_jobs()

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
                            f"Vault: {job.sn_liquidity_manager_address} | "
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
                    f"Currently running {len(running_jobs)} job(s): {list(running_jobs.keys())}"
                )

                await asyncio.sleep(check_interval)

            except Exception as e:
                logger.error(f"Error in job monitor: {e}", exc_info=True)
                await asyncio.sleep(check_interval)

    async def monitor_and_set_weights():
        """Continuously calculate and set weights."""
        weight_set_interval = 1200  # 20 mins
        
        while True:
            try:
                logger.info("Running weight setting cycle...")
                await emissions_service.set_weights_on_chain(wallet, config["netuid"])
                await asyncio.sleep(weight_set_interval)
            except Exception as e:
                logger.error(f"Error in weight setter: {e}")
                await asyncio.sleep(60)

    try:
        # Run the job monitor and weight setter concurrently
        await asyncio.gather(
            monitor_and_run_jobs(),
            monitor_and_set_weights(),
        )

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
        validate_config(config)
        asyncio.run(run_jobs_validator(config))
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

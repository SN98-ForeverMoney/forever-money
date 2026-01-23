
import asyncio
import logging
import time
import subprocess
import os
import sys
from unittest.mock import MagicMock, AsyncMock

import bittensor as bt
from tortoise import Tortoise

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from validator.round_orchestrator import AsyncRoundOrchestrator
from validator.models.job import Job, Round, RoundType
from validator.repositories.job import JobRepository
from protocol import Inventory, Position

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Constants
MINER_PORT = 8092
MINER_IP = "127.0.0.1"

async def start_miner():
    """Start the miner process."""
    env = os.environ.copy()
    env["AXON_PORT"] = str(MINER_PORT)
    
    cmd = [
        sys.executable, "-u", "-m", "miner.miner",
        "--wallet.name", "test_miner",
        "--wallet.hotkey", "test_hotkey",
        "--wallet.path", "./wallets",
        "--axon.port", str(MINER_PORT),
        "--subtensor.network", "test" # Won't connect but needed for init
    ]
    
    logger.info(f"Starting miner on port {MINER_PORT}...")
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env
    )
    
    # Wait for miner to start
    await asyncio.sleep(5)
    return process

def setup_wallets():
    """Create test wallets."""
    os.makedirs("./wallets", exist_ok=True)
    
    # Validator wallet
    val_wallet = bt.Wallet(name="test_validator", hotkey="test_validator_hotkey", path="./wallets")
    val_wallet.create_if_non_existent(coldkey_use_password=False, hotkey_use_password=False)
    logger.info(f"Validator wallet ready: {val_wallet.hotkey.ss58_address}")

    # Miner wallet
    miner_wallet = bt.Wallet(name="test_miner", hotkey="test_hotkey", path="./wallets")
    miner_wallet.create_if_non_existent(coldkey_use_password=False, hotkey_use_password=False)
    logger.info(f"Miner wallet ready: {miner_wallet.hotkey.ss58_address}")
    return val_wallet

async def test_full_flow():
    miner_process = None
    try:
        # 0. Setup Wallets
        val_wallet = setup_wallets()

        # 1. Start Miner
        miner_process = await start_miner()
        
        # 2. Setup Validator Environment
        # Mock Metagraph
        metagraph = MagicMock(spec=bt.Metagraph)
        metagraph.hotkeys = ["test_hotkey"] # Miner's hotkey
        metagraph.S = [1.0] # Active
        
        # Create AxonInfo for the miner
        # We need the miner's hotkey address. 
        miner_wallet = bt.Wallet(name="test_miner", hotkey="test_hotkey", path="./wallets")
        
        axon_info = bt.AxonInfo(
            version=1,
            ip=MINER_IP,
            port=MINER_PORT,
            ip_type=4,
            hotkey=miner_wallet.hotkey.ss58_address, 
            coldkey=miner_wallet.coldkeypub.ss58_address
        )
        metagraph.axons = [axon_info]
        # Make sure metagraph.hotkeys matches the miner's address
        metagraph.hotkeys = [miner_wallet.hotkey.ss58_address]

        # Mock Dendrite (Real Dendrite needed to talk to miner?)
        dendrite = bt.Dendrite(wallet=val_wallet)
        
        # Init DB (InMemory)
        await Tortoise.init(
            db_url="sqlite://:memory:",
            modules={"models": ["validator.models.job"]}
        )
        await Tortoise.generate_schemas(safe=True)
        
        job_repo = JobRepository()
        
        # Create a Test Job
        job = await Job.create(
            job_id="test_job_1",
            sn_liquidity_manager_address="0x123",
            pair_address="0x456",
            chain_id=8453,
            fee_rate=0.003,
            round_duration_seconds=60
        )
        
        # Mock Dependencies for Orchestrator
        # We need to mock SnLiqManagerService because we don't have a real chain connection
        # and we need to mock PoolDataDB because we don't have Postgres
        
        with  unittest.mock.patch("validator.round_orchestrator.SnLiqManagerService") as MockLiqManager, \
              unittest.mock.patch("validator.round_orchestrator.PoolDataDB") as MockPoolDB, \
              unittest.mock.patch("validator.round_orchestrator.AsyncWeb3Helper") as MockWeb3:
            
            # Setup LiqManager Mock
            liq_instance = MockLiqManager.return_value
            liq_instance.get_inventory = AsyncMock(return_value=Inventory(amount0="1000000000000000000", amount1="1000000000000000000"))
            liq_instance.get_current_positions = AsyncMock(return_value=[]) # No initial positions
            liq_instance.get_current_price = AsyncMock(return_value=79228162514264337593543950336) # Price 1.0 (Q96)
            
            # Setup PoolDB Mock
            db_instance = MockPoolDB.return_value
            # Return some dummy swap events for backtest
            db_instance.get_swap_events = AsyncMock(return_value=[
                {
                    "evt_block_number": 250,
                    "sqrt_price_x96": 79228162514264337593543950336,
                    "amount0": 1000,
                    "amount1": -1000,
                    "liquidity": 1000000,
                    "tick": 0
                }
            ])
            db_instance.get_sqrt_price_at_block = AsyncMock(return_value=79228162514264337593543950336)
            
            # Initialize Orchestrator
            config = {
                "executor_bot_url": None,
                "rebalance_check_interval": 10
            }
            orchestrator = AsyncRoundOrchestrator(
                job_repository=job_repo,
                dendrite=dendrite,
                metagraph=metagraph,
                config=config
            )

            # Mock get_latest_block
            # Sequence: Initial(200) -> Loop1(210) -> Loop2(300) -> Exit (deadline check is separate but loop depends on block for progress?)
            # The loop condition is time based.
            # But we want to simulate block progression.
            # orchestrator._get_latest_block is called:
            # 1. At start of run_evaluation_round (target block) -> 200
            # 2. Inside loop: current_block = await self._get_latest_block(...)
            
            # We need to make sure the loop eventually finishes or we mock time?
            # The loop is: while round_.round_deadline >= datetime.now()
            # We can't easily mock datetime.now() without Freezegun or similar.
            # But we can make the loop break if we check block number? No, code doesn't check block limit.
            
            # We can mock asyncio.sleep to advance time?
            # Or we can just let it run a few iterations.
            # Wait, if I mock _get_latest_block to return same value, it loops fast (with sleep(1) if not rebalancing).
            # If I want to test rebalancing, I need to hit the interval.
            
            orchestrator._get_latest_block = AsyncMock(side_effect=[200, 210, 300] + [300]*100) 
            
            # We also need to force the loop to exit.
            # We can patch datetime to jump forward?
            # Or we can make _get_latest_block raise an exception to break the loop? No.
            # We can rely on round_duration_seconds=60.
            # If we don't advance time, it will run for 60 real seconds.
            # That's too long for a test.
            
            # Hack: modify round_deadline to be in the past after a few iterations?
            # Or use a shorter duration.
            job.round_duration_seconds = 60
            await job.save()
            
            # Initialize round numbers
            await orchestrator._initialize_round_numbers(job)

            logger.info("Running evaluation round...")
            
            # Patch datetime in round_orchestrator to control loop
            from datetime import datetime, timedelta, timezone
            start_dt = datetime.now(timezone.utc)
            
            def dt_side_effect(tz=None):
                nonlocal start_dt
                start_dt += timedelta(seconds=15) # Advance 15s each call
                return start_dt

            with unittest.mock.patch("validator.round_orchestrator.datetime") as mock_dt:
                mock_dt.now.side_effect = dt_side_effect
                mock_dt.timezone = timezone
                
                await orchestrator.run_evaluation_round(job)
            
            # Verify results
            # Check if round was completed
            rounds = await Round.filter(job=job).all()
            assert len(rounds) == 1
            r = rounds[0]
            assert r.status == "completed"
            
            # Check if miner participated
            # We need to query the rebalance decision
            # Check Prediction table directly
            from validator.models.job import Prediction
            predictions = await Prediction.filter(round=r).all()
            assert len(predictions) >= 1
            
            logger.info("Round completed successfully!")
            logger.info(f"Winner: {r.winner_uid}")
            
            # Since we only have 1 miner and it should accept, it should be the winner (or at least participated)
            assert r.winner_uid == 0
            
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        if miner_process:
            logger.info("Miner stdout:")
            print(miner_process.stdout.read().decode())
            logger.info("Miner stderr:")
            print(miner_process.stderr.read().decode())
        raise
    finally:
        if miner_process:
            logger.info("Stopping miner...")
            miner_process.terminate()
            miner_process.wait()
        
        # Only close if initialized
        try:
            if Tortoise._inited:
                await Tortoise.close_connections()
        except Exception:
            pass

if __name__ == "__main__":
    import unittest.mock
    asyncio.run(test_full_flow())

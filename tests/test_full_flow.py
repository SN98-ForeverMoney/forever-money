"""
Full flow integration test for SN98 ForeverMoney.

Tests the complete validator-miner interaction including:
- Miner startup and axon serving
- Vault registration via VaultRegistrationQuery
- Evaluation round execution
- Miner scoring and winner selection
"""

import asyncio
import logging
import os
import subprocess
import sys
import unittest.mock
from unittest.mock import MagicMock, AsyncMock
from decimal import Decimal

import bittensor as bt
import pytest
from tortoise import Tortoise

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from validator.round_orchestrator import AsyncRoundOrchestrator
from validator.models.job import Job, Round
from validator.models.miner_vault import MinerVault, VaultSnapshot
from validator.repositories.job import JobRepository
from validator.services.vault import VaultService
from protocol import Inventory
from protocol.synapses import VaultRegistrationQuery

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Constants
MINER_PORT = 8092
MINER_IP = "127.0.0.1"
TEST_VAULT_ADDRESS = "0x1234567890abcdef1234567890abcdef12345678"


async def start_miner(vault_address: str = None):
    """Start the miner process with optional vault configuration."""
    env = os.environ.copy()
    env["AXON_PORT"] = str(MINER_PORT)

    # Configure miner's vault if provided
    if vault_address:
        env["MINER_VAULT_ADDRESS"] = vault_address
        env["MINER_VAULT_CHAIN_ID"] = "8453"

    cmd = [
        sys.executable, "-u", "-m", "miner.miner",
        "--wallet.name", "test_miner",
        "--wallet.hotkey", "test_hotkey",
        "--wallet.path", "./wallets",
        "--axon.port", str(MINER_PORT),
        "--subtensor.network", "test"  # Won't connect but needed for init
    ]

    logger.info(f"Starting miner on port {MINER_PORT}...")
    if vault_address:
        logger.info(f"Miner vault configured: {vault_address}")

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
    return val_wallet, miner_wallet


@pytest.mark.asyncio
async def test_full_flow():
    """Test complete validator flow including vault registration."""
    miner_process = None
    try:
        # 0. Setup Wallets
        val_wallet, miner_wallet = setup_wallets()
        miner_hotkey = miner_wallet.hotkey.ss58_address

        # 1. Start Miner with vault configured
        miner_process = await start_miner(vault_address=TEST_VAULT_ADDRESS)

        # 2. Setup Validator Environment
        # Mock Metagraph
        metagraph = MagicMock(spec=bt.Metagraph)
        metagraph.S = [1.0]  # Active miner
        metagraph.hotkeys = [miner_hotkey]

        # Create AxonInfo for the miner
        axon_info = bt.AxonInfo(
            version=1,
            ip=MINER_IP,
            port=MINER_PORT,
            ip_type=4,
            hotkey=miner_hotkey,
            coldkey=miner_wallet.coldkeypub.ss58_address
        )
        metagraph.axons = [axon_info]

        # Real Dendrite to talk to miner
        dendrite = bt.Dendrite(wallet=val_wallet)

        # Init DB (InMemory) - include miner_vault models
        await Tortoise.init(
            db_url="sqlite://:memory:",
            modules={"models": ["validator.models.job", "validator.models.miner_vault"]}
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
        with unittest.mock.patch("validator.round_orchestrator.SnLiqManagerService") as MockLiqManager, \
             unittest.mock.patch("validator.round_orchestrator.PoolDataDB") as MockPoolDB, \
             unittest.mock.patch("validator.round_orchestrator.AsyncWeb3Helper") as MockWeb3, \
             unittest.mock.patch("validator.services.vault.AsyncWeb3Helper") as MockVaultWeb3:

            # Setup LiqManager Mock
            liq_instance = MockLiqManager.return_value
            liq_instance.get_inventory = AsyncMock(
                return_value=Inventory(amount0="1000000000000000000", amount1="1000000000000000000")
            )
            liq_instance.get_current_positions = AsyncMock(return_value=[])
            liq_instance.get_current_price = AsyncMock(return_value=79228162514264337593543950336)

            # Setup PoolDB Mock
            db_instance = MockPoolDB.return_value
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

            # Setup Vault Web3 Mock for associatedMiner verification
            # Convert miner hotkey to bytes32 for mock response
            from validator.utils.crypto import ss58_to_bytes32
            miner_bytes32 = ss58_to_bytes32(miner_hotkey)

            mock_vault_contract = MagicMock()
            mock_vault_contract.functions.associatedMiner.return_value.call = AsyncMock(
                return_value=miner_bytes32
            )

            mock_vault_web3_instance = MagicMock()
            mock_vault_web3_instance.make_contract_by_name.return_value = mock_vault_contract
            MockVaultWeb3.make_web3.return_value = mock_vault_web3_instance

            # Initialize Orchestrator with vault requirement enabled
            config = {
                "executor_bot_url": None,
                "rebalance_check_interval": 10,
                "require_vault_for_evaluation": True,  # Enable vault requirement
            }
            orchestrator = AsyncRoundOrchestrator(
                job_repository=job_repo,
                dendrite=dendrite,
                metagraph=metagraph,
                config=config
            )

            # Mock _get_latest_block for controlled progression
            orchestrator._get_latest_block = AsyncMock(side_effect=[200, 210, 300] + [300] * 100)

            # Initialize round numbers
            await orchestrator._initialize_round_numbers(job)

            # ============================================
            # TEST VAULT REGISTRATION FLOW
            # ============================================
            logger.info("=" * 60)
            logger.info("Testing vault registration flow...")
            logger.info("=" * 60)

            # Verify no miners are registered yet
            registered_before = await MinerVault.all()
            assert len(registered_before) == 0, "Expected no vaults registered initially"
            logger.info("Verified: No vaults registered initially")

            # Run the registration check (this queries miners for their vault info)
            await orchestrator._check_and_register_new_miners_in_db()

            # Verify miner's vault was registered
            registered_after = await MinerVault.all()
            assert len(registered_after) == 1, f"Expected 1 vault registered, got {len(registered_after)}"

            vault = registered_after[0]
            assert vault.miner_uid == 0
            assert vault.miner_hotkey == miner_hotkey
            assert vault.vault_address == TEST_VAULT_ADDRESS.lower()
            assert vault.is_verified is True, "Vault should be verified (associatedMiner matched)"
            logger.info(f"Verified: Vault registered and verified for miner 0")
            logger.info(f"  Address: {vault.vault_address}")
            logger.info(f"  Verified: {vault.is_verified}")

            # Add a balance snapshot so miner passes minimum balance check
            await VaultSnapshot.create(
                vault=vault,
                token0_balance=Decimal("1000000000000000000"),
                token1_balance=Decimal("1000000000000000000"),
                total_value_usd=Decimal("5000.00"),  # Above $1000 minimum
                block_number=200,
            )
            logger.info("Added vault snapshot with $5000 balance")

            # ============================================
            # TEST EVALUATION ROUND WITH VAULT GATING
            # ============================================
            logger.info("=" * 60)
            logger.info("Running evaluation round with vault gating...")
            logger.info("=" * 60)

            # Patch datetime to control loop duration
            from datetime import datetime, timedelta, timezone
            start_dt = datetime.now(timezone.utc)

            def dt_side_effect(tz=None):
                nonlocal start_dt
                start_dt += timedelta(seconds=15)
                return start_dt

            with unittest.mock.patch("validator.round_orchestrator.datetime") as mock_dt:
                mock_dt.now.side_effect = dt_side_effect
                mock_dt.timezone = timezone

                await orchestrator.run_evaluation_round(job)

            # Verify results
            rounds = await Round.filter(job=job).all()
            assert len(rounds) == 1, f"Expected 1 round, got {len(rounds)}"

            r = rounds[0]
            assert r.status == "completed", f"Expected completed, got {r.status}"
            logger.info(f"Round completed with status: {r.status}")

            # Check if miner participated
            from validator.models.job import Prediction
            predictions = await Prediction.filter(round=r).all()
            assert len(predictions) >= 1, "Expected at least 1 prediction"
            logger.info(f"Miner participated with {len(predictions)} prediction(s)")

            # Since we only have 1 miner with a valid vault, it should be the winner
            assert r.winner_uid == 0, f"Expected winner_uid=0, got {r.winner_uid}"
            logger.info(f"Winner: Miner {r.winner_uid}")

            logger.info("=" * 60)
            logger.info("FULL FLOW TEST PASSED!")
            logger.info("=" * 60)

    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        if miner_process:
            logger.info("Miner stdout:")
            stdout = miner_process.stdout.read()
            if stdout:
                print(stdout.decode())
            logger.info("Miner stderr:")
            stderr = miner_process.stderr.read()
            if stderr:
                print(stderr.decode())
        raise
    finally:
        if miner_process:
            logger.info("Stopping miner...")
            miner_process.terminate()
            miner_process.wait()

        # Clean up Tortoise
        try:
            if Tortoise._inited:
                await Tortoise.close_connections()
        except Exception:
            pass


@pytest.mark.asyncio
async def test_vault_registration_without_miner_vault():
    """Test that miners without vaults are not registered."""
    try:
        # Setup wallets
        os.makedirs("./wallets", exist_ok=True)
        val_wallet = bt.Wallet(name="test_validator2", hotkey="test_validator_hotkey2", path="./wallets")
        val_wallet.create_if_non_existent(coldkey_use_password=False, hotkey_use_password=False)

        miner_wallet = bt.Wallet(name="test_miner2", hotkey="test_hotkey2", path="./wallets")
        miner_wallet.create_if_non_existent(coldkey_use_password=False, hotkey_use_password=False)
        miner_hotkey = miner_wallet.hotkey.ss58_address

        # Mock metagraph
        metagraph = MagicMock(spec=bt.Metagraph)
        metagraph.S = [1.0]
        metagraph.hotkeys = [miner_hotkey]
        metagraph.axons = [MagicMock()]

        # Mock dendrite to return a response with no vault
        mock_response = VaultRegistrationQuery(has_vault=False)
        dendrite = AsyncMock(return_value=[mock_response])

        # Init DB
        await Tortoise.init(
            db_url="sqlite://:memory:",
            modules={"models": ["validator.models.job", "validator.models.miner_vault"]}
        )
        await Tortoise.generate_schemas(safe=True)

        job_repo = JobRepository()

        config = {"require_vault_for_evaluation": True}
        orchestrator = AsyncRoundOrchestrator(
            job_repository=job_repo,
            dendrite=dendrite,
            metagraph=metagraph,
            config=config
        )

        # Run registration check
        await orchestrator._check_and_register_new_miners_in_db()

        # Verify no vault was registered
        vaults = await MinerVault.all()
        assert len(vaults) == 0, "No vault should be registered when miner has no vault"
        logger.info("Verified: Miner without vault was not registered")

    finally:
        try:
            if Tortoise._inited:
                await Tortoise.close_connections()
        except Exception:
            pass


@pytest.mark.asyncio
async def test_vault_filtering_excludes_unregistered_miners():
    """Test that miners without vaults are excluded from evaluation when vault requirement is enabled."""
    try:
        # Setup
        os.makedirs("./wallets", exist_ok=True)
        val_wallet = bt.Wallet(name="test_validator3", hotkey="test_validator_hotkey3", path="./wallets")
        val_wallet.create_if_non_existent(coldkey_use_password=False, hotkey_use_password=False)

        # Mock metagraph with 3 miners
        metagraph = MagicMock(spec=bt.Metagraph)
        metagraph.S = [1.0, 1.0, 1.0]  # 3 active miners
        metagraph.hotkeys = ["hotkey0", "hotkey1", "hotkey2"]
        metagraph.axons = [MagicMock(), MagicMock(), MagicMock()]

        # Mock dendrite - all miners report no vault
        mock_response = VaultRegistrationQuery(has_vault=False)
        dendrite = AsyncMock(return_value=[mock_response])

        # Init DB
        await Tortoise.init(
            db_url="sqlite://:memory:",
            modules={"models": ["validator.models.job", "validator.models.miner_vault"]}
        )
        await Tortoise.generate_schemas(safe=True)

        job_repo = JobRepository()

        # Create job
        job = await Job.create(
            job_id="test_job_filter",
            sn_liquidity_manager_address="0x123",
            pair_address="0x456",
            chain_id=8453,
            fee_rate=0.003,
            round_duration_seconds=60
        )

        config = {"require_vault_for_evaluation": True}
        orchestrator = AsyncRoundOrchestrator(
            job_repository=job_repo,
            dendrite=dendrite,
            metagraph=metagraph,
            config=config
        )

        # Mock vault service to return empty eligible list
        orchestrator.vault_service.filter_eligible_miners = AsyncMock(return_value=[])

        await orchestrator._initialize_round_numbers(job)

        # Run evaluation round - should exit early due to no eligible miners
        with unittest.mock.patch("validator.round_orchestrator.SnLiqManagerService"):
            await orchestrator.run_evaluation_round(job)

        # Verify no round was created (exited early)
        rounds = await Round.filter(job=job).all()
        assert len(rounds) == 0, "No round should be created when no miners have eligible vaults"
        logger.info("Verified: Evaluation round skipped when no miners have eligible vaults")

    finally:
        try:
            if Tortoise._inited:
                await Tortoise.close_connections()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(test_full_flow())

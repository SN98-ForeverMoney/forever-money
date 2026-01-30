
import unittest
import asyncio
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import httpx
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timedelta, timezone

from tortoise import Tortoise

from validator.round_orchestrator import AsyncRoundOrchestrator
from validator.orchestrator.executor import execute_strategy_onchain
from validator.models.job import Job, Round, LiveExecution, MinerScore, RoundType, RoundStatus
from validator.repositories.job import JobRepository
from validator.utils.env import (
    JOBS_POSTGRES_HOST,
    JOBS_POSTGRES_PORT,
    JOBS_POSTGRES_DB,
    JOBS_POSTGRES_USER,
    JOBS_POSTGRES_PASSWORD,
)
from protocol.models import Inventory, Position
from protocol.synapses import RebalanceQuery

class TestLiveFlow(unittest.IsolatedAsyncioTestCase):
    """Test live flow with REAL Postgres DB (not mocks)."""
    
    async def asyncSetUp(self):
        # Connect to real DB
        db_url = (
            f"postgres://{JOBS_POSTGRES_USER}:{JOBS_POSTGRES_PASSWORD}@"
            f"{JOBS_POSTGRES_HOST}:{JOBS_POSTGRES_PORT}/{JOBS_POSTGRES_DB}"
        )
        
        await Tortoise.init(
            db_url=db_url,
            modules={
                "models": [
                    "validator.models.job",
                    "validator.models.pool_events",
                ],
            },
        )
        await Tortoise.generate_schemas(safe=True)
        
        # Use REAL repository (not mock)
        self.repo = JobRepository()
        
        # Mock external dependencies (dendrite, metagraph, web3, liq manager)
        self.mock_dendrite = AsyncMock()
        self.mock_metagraph = MagicMock()
        self.mock_metagraph.hotkeys = ["hotkey0", "hotkey1"]
        self.mock_metagraph.axons = [MagicMock(), MagicMock()]
        self.mock_metagraph.S = [1.0, 1.0]
        
        self.config = {
            "rebalance_check_interval": 1,
            "executor_bot_url": "http://localhost:8000",
            "executor_bot_api_key": "test_key"
        }
        
        # Patch web3 helper
        self.patcher_web3 = patch("validator.round_orchestrator.AsyncWeb3Helper")
        self.mock_web3_cls = self.patcher_web3.start()
        self.mock_web3 = self.mock_web3_cls.make_web3.return_value
        self.mock_web3.web3.eth.block_number = AsyncMock(return_value=100)
        
        # Patch liq manager
        self.patcher_liq = patch("validator.round_orchestrator.SnLiqManagerService")
        self.mock_liq_cls = self.patcher_liq.start()
        self.mock_liq = self.mock_liq_cls.return_value
        self.mock_liq.get_inventory = AsyncMock(return_value=Inventory(amount0="1000", amount1="1000"))
        self.mock_liq.get_current_positions = AsyncMock(return_value=[])
        self.mock_liq.get_current_price = AsyncMock(return_value=1.0)
        
        # Initialize orchestrator with REAL repo
        self.orchestrator = AsyncRoundOrchestrator(
            self.repo, self.mock_dendrite, self.mock_metagraph, self.config
        )
        # Mock backtester
        self.orchestrator.backtester = AsyncMock()
        self.orchestrator.backtester.evaluate_positions_performance.return_value = {
            "pnl": 0.1,
            "initial_value": 1000,
            "final_value": 1100,
            "initial_inventory": Inventory(amount0="1000", amount1="1000"),
            "final_inventory": Inventory(amount0="1000", amount1="1000")
        }

    async def asyncTearDown(self):
        self.patcher_web3.stop()
        self.patcher_liq.stop()
        
        # Clean up test data (optional)
        CLEANUP_TEST_DATA = False  # Set to True to clean up after tests
        if CLEANUP_TEST_DATA:
            try:
                # Clean up test jobs (and cascading deletes will clean up rounds, executions, etc.)
                await Job.filter(job_id__startswith="test_job").delete()
                print("[TestLiveFlow] Cleaned up test data")
            except Exception as e:
                print(f"Warning: Failed to clean up test data: {e}")
        
        if Tortoise._inited:
            await Tortoise.close_connections()

    async def test_run_live_round_eligible(self):
        # Create Job in REAL DB
        job = await Job.create(
            job_id=f"test_job_{int(datetime.now(timezone.utc).timestamp())}",
            sn_liquidity_manager_address="0x123",
            pair_address="0x456",
            chain_id=8453,
            round_duration_seconds=60,
            fee_rate=3000
        )
        
        # Initialize round numbers
        self.orchestrator.round_numbers[job.job_id] = {"evaluation": 0, "live": 0}
        
        # Create a previous evaluation round with a winner in REAL DB
        eval_round = await self.repo.create_round(
            job=job,
            round_type=RoundType.EVALUATION,
            round_number=1,
            start_block=90
        )
        eval_round.winner_uid = 0
        eval_round.status = RoundStatus.COMPLETED
        await eval_round.save()
        
        # Create MinerScore to make miner eligible (7+ days participation)
        await MinerScore.create(
            job=job,
            miner_uid=0,
            miner_hotkey="hotkey0",
            evaluation_score=0.8,
            live_score=0.0,
            combined_score=0.8,
            is_eligible_for_live=True,
            participation_days=10  # > 7 days, so eligible
        )
        
        # Mock Block Updates (simulation loop)
        # Use a function to return incrementing blocks
        block_counter = 100
        async def get_next_block(*args):
            nonlocal block_counter
            block_counter += 1
            return block_counter
            
        self.orchestrator._get_latest_block = AsyncMock(side_effect=get_next_block)
        
        # Mock Miner Response
        response = MagicMock(spec=RebalanceQuery)
        response.accepted = True
        response.desired_positions = [Position(tick_lower=-100, tick_upper=100, allocation0="500", allocation1="500")]
        self.mock_dendrite.return_value = [response]
        
        # Mock Execution with httpx
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"tx_hash": "0x123"}
        mock_response.text = ""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_async_client = MagicMock()
        mock_async_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_async_client.return_value.__aexit__ = AsyncMock(return_value=None)

        with patch("validator.orchestrator.executor.httpx.AsyncClient", mock_async_client):
            # Run Live Round
            await self.orchestrator.run_live_round(job)
            
            # Assertions - check REAL DB records
            # Check round was created in DB
            live_rounds = await Round.filter(job=job, round_type=RoundType.LIVE).all()
            self.assertGreater(len(live_rounds), 0, "Live round should be created in DB")
            live_round = live_rounds[0]
            
            # Check execution was called
            mock_client.post.assert_called()
            
            # Check LiveExecution was created in REAL DB
            executions = await LiveExecution.filter(round_id=live_round.id).all()
            self.assertGreater(len(executions), 0, "LiveExecution should be created in DB")
            
            # Check round is completed
            await live_round.refresh_from_db()
            self.assertEqual(live_round.status, RoundStatus.COMPLETED, "Round should be completed")

    async def test_run_live_round_not_eligible(self):
        # Create Job in REAL DB
        job = await Job.create(
            job_id=f"test_job_not_eligible_{int(datetime.now(timezone.utc).timestamp())}",
            sn_liquidity_manager_address="0x123",
            pair_address="0x456",
            chain_id=8453,
            round_duration_seconds=60,
            fee_rate=3000
        )
        
        # Create evaluation round with winner, but miner not eligible (< 7 days)
        eval_round = await self.repo.create_round(
            job=job,
            round_type=RoundType.EVALUATION,
            round_number=1,
            start_block=90
        )
        eval_round.winner_uid = 0
        await eval_round.save()
        
        # Create MinerScore with < 7 days participation (not eligible)
        await MinerScore.create(
            job=job,
            miner_uid=0,
            miner_hotkey="hotkey0",
            evaluation_score=0.8,
            live_score=0.0,
            combined_score=0.8,
            participation_days=5  # < 7 days, so NOT eligible
        )
        
        await self.orchestrator.run_live_round(job)
        
        # Check no live round was created
        live_rounds = await Round.filter(job=job, round_type=RoundType.LIVE).all()
        self.assertEqual(len(live_rounds), 0, "No live round should be created for ineligible miner")

    async def test_execute_strategy_onchain_success(self):
        """Test successful execution via executor bot."""
        # Create Job and Round in REAL DB
        job = await Job.create(
            job_id=f"test_job_exec_success_{int(datetime.now(timezone.utc).timestamp())}",
            sn_liquidity_manager_address="0x123",
            pair_address="0x456",
            chain_id=8453,
            round_duration_seconds=60,
            fee_rate=3000
        )
        
        round_obj = await self.repo.create_round(
            job=job,
            round_type=RoundType.LIVE,
            round_number=1,
            start_block=100
        )
        
        position = Position(tick_lower=-100, tick_upper=100, allocation0="500", allocation1="500")
        rebalance_history = [{"new_positions": [position]}]
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"tx_hash": "0xabc123"}
        mock_response.text = ""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_async_client = MagicMock()
        mock_async_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_async_client.return_value.__aexit__ = AsyncMock(return_value=None)

        with patch("validator.orchestrator.executor.httpx.AsyncClient", mock_async_client):
            result = await execute_strategy_onchain(
                self.repo,
                self.config,
                job,
                round_obj,
                0,
                rebalance_history,
            )
            
            assert result["success"] is True
            assert result["tx_hash"] == "0xabc123"
            assert result["error"] is None
            assert result["execution_id"] is not None
            
            # Verify LiveExecution was created in REAL DB
            execution = await LiveExecution.get(execution_id=result["execution_id"])
            self.assertEqual(execution.round_id, round_obj.id)
            self.assertEqual(execution.job_id, job.id)
            self.assertEqual(execution.miner_uid, 0)
            self.assertEqual(execution.tx_hash, "0xabc123")
            self.assertEqual(execution.tx_status, "pending")

    async def test_execute_strategy_onchain_failure(self):
        """Test failed execution via executor bot."""
        # Create Job and Round in REAL DB
        job = await Job.create(
            job_id=f"test_job_exec_failure_{int(datetime.now(timezone.utc).timestamp())}",
            sn_liquidity_manager_address="0x123",
            pair_address="0x456",
            chain_id=8453,
            round_duration_seconds=60,
            fee_rate=3000
        )
        
        round_obj = await self.repo.create_round(
            job=job,
            round_type=RoundType.LIVE,
            round_number=1,
            start_block=100
        )
        
        position = Position(tick_lower=-100, tick_upper=100, allocation0="500", allocation1="500")
        rebalance_history = [{"new_positions": [position]}]
        
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_async_client = MagicMock()
        mock_async_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_async_client.return_value.__aexit__ = AsyncMock(return_value=None)

        with patch("validator.orchestrator.executor.httpx.AsyncClient", mock_async_client):
            result = await execute_strategy_onchain(
                self.repo,
                self.config,
                job,
                round_obj,
                0,
                rebalance_history,
            )

            assert result["success"] is False
            assert result["tx_hash"] is None
            assert "Executor bot returned status 500" in result["error"]
            assert result["execution_id"] is not None  # Should still create execution record
            
            # Verify LiveExecution was created in REAL DB with failed status
            execution = await LiveExecution.get(execution_id=result["execution_id"])
            self.assertEqual(execution.round_id, round_obj.id)
            self.assertEqual(execution.job_id, job.id)
            self.assertEqual(execution.miner_uid, 0)
            self.assertIsNone(execution.tx_hash)
            self.assertEqual(execution.tx_status, "failed")
            self.assertIsNotNone(execution.actual_performance)
            self.assertIn("error", execution.actual_performance)

    async def test_execute_strategy_onchain_network_error(self):
        """Test network error during execution."""
        # Create Job and Round in REAL DB
        job = await Job.create(
            job_id=f"test_job_exec_network_error_{int(datetime.now(timezone.utc).timestamp())}",
            sn_liquidity_manager_address="0x123",
            pair_address="0x456",
            chain_id=8453,
            round_duration_seconds=60,
            fee_rate=3000
        )
        
        round_obj = await self.repo.create_round(
            job=job,
            round_type=RoundType.LIVE,
            round_number=1,
            start_block=100
        )
        
        position = Position(tick_lower=-100, tick_upper=100, allocation0="500", allocation1="500")
        rebalance_history = [{"new_positions": [position]}]
        
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.RequestError("Connection failed"))
        mock_async_client = MagicMock()
        mock_async_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_async_client.return_value.__aexit__ = AsyncMock(return_value=None)

        with patch("validator.orchestrator.executor.httpx.AsyncClient", mock_async_client):
            result = await execute_strategy_onchain(
                self.repo,
                self.config,
                job,
                round_obj,
                0,
                rebalance_history,
            )
            
            assert result["success"] is False
            assert result["tx_hash"] is None
            assert "HTTP client error" in result["error"]
            assert result["execution_id"] is not None  # Should still create execution record
            
            # Verify LiveExecution was created in REAL DB with failed status
            execution = await LiveExecution.get(execution_id=result["execution_id"])
            self.assertEqual(execution.round_id, round_obj.id)
            self.assertEqual(execution.job_id, job.id)
            self.assertEqual(execution.miner_uid, 0)
            self.assertIsNone(execution.tx_hash)
            self.assertEqual(execution.tx_status, "failed")
            self.assertIsNotNone(execution.actual_performance)
            self.assertIn("error", execution.actual_performance)

if __name__ == "__main__":
    unittest.main()

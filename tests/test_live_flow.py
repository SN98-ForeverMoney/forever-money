
import unittest
import asyncio
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import requests
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timedelta, timezone

from validator.round_orchestrator import AsyncRoundOrchestrator
from validator.models.job import Job, Round, RoundType, RoundStatus
from validator.repositories.job import JobRepository
from protocol.models import Inventory, Position
from protocol.synapses import RebalanceQuery

class TestLiveFlow(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Mocks
        self.mock_repo = AsyncMock(spec=JobRepository)
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
        
        # Initialize orchestrator
        # We need to patch database objects and web3 helper
        self.patcher_web3 = patch("validator.round_orchestrator.AsyncWeb3Helper")
        self.mock_web3_cls = self.patcher_web3.start()
        self.mock_web3 = self.mock_web3_cls.make_web3.return_value
        self.mock_web3.web3.eth.block_number = 100
        
        self.patcher_liq = patch("validator.round_orchestrator.SnLiqManagerService")
        self.mock_liq_cls = self.patcher_liq.start()
        self.mock_liq = self.mock_liq_cls.return_value
        self.mock_liq.get_inventory = AsyncMock(return_value=Inventory(amount0="1000", amount1="1000"))
        self.mock_liq.get_current_positions = AsyncMock(return_value=[])
        self.mock_liq.get_current_price = AsyncMock(return_value=1.0)
        
        self.orchestrator = AsyncRoundOrchestrator(
            self.mock_repo, self.mock_dendrite, self.mock_metagraph, self.config
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

    async def test_run_live_round_eligible(self):
        # Setup Job
        job = Job(
            job_id="test_job",
            sn_liquidity_manager_address="0x123",
            pair_address="0x456",
            chain_id=8453,
            round_duration_seconds=60,
            fee_rate=3000
        )
        
        # Initialize round numbers
        self.orchestrator.round_numbers["test_job"] = {"evaluation": 0, "live": 0}
        
        # Mock Previous Winner
        self.mock_repo.get_previous_winner.return_value = 0 # Miner 0
        
        # Mock Eligibility
        mock_score = MagicMock()
        mock_score.miner_uid = 0
        self.mock_repo.get_eligible_miners.return_value = [mock_score]
        
        # Mock Round Creation
        round_obj = MagicMock(spec=Round)
        round_obj.round_id = "round_1"
        round_obj.job = job
        round_obj.round_type = RoundType.LIVE
        round_obj.round_number = 1
        round_obj.start_block = 100
        round_obj.start_time = datetime.now(timezone.utc)
        round_obj.round_deadline = datetime.now(timezone.utc) + timedelta(seconds=1)
        round_obj.status = RoundStatus.ACTIVE
        
        self.mock_repo.create_round.return_value = round_obj
        
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
        
        # Mock Execution with requests
        with patch("requests.post") as mock_post:
            # Mock successful response
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"tx_hash": "0x123"}
            mock_response.text = ""
            
            mock_post.return_value = mock_response
            
            # Run Live Round
            await self.orchestrator.run_live_round(job)
            
            # Assertions
            self.mock_repo.get_previous_winner.assert_called_with("test_job")
            self.mock_repo.create_round.assert_called()
            
            # Check execution called
            mock_post.assert_called()
            
            # Check score updated
            self.mock_repo.update_miner_score.assert_called()
            call_kwargs = self.mock_repo.update_miner_score.call_args[1]
            assert call_kwargs["miner_uid"] == 0
            assert call_kwargs["round_type"] == RoundType.LIVE
            
            # Check LiveExecution was created
            self.mock_repo.create_live_execution.assert_called()
            
            # Check completion
            self.mock_repo.complete_round.assert_called()

    async def test_run_live_round_not_eligible(self):
        job = Job(job_id="test_job", chain_id=1)
        self.mock_repo.get_previous_winner.return_value = 0
        self.mock_repo.get_eligible_miners.return_value = [] # No eligible miners
        
        await self.orchestrator.run_live_round(job)
        
        self.mock_repo.create_round.assert_not_called()

    async def test_execute_strategy_onchain_success(self):
        """Test successful execution via executor bot."""
        job = Job(
            job_id="test_job",
            sn_liquidity_manager_address="0x123",
            pair_address="0x456",
            chain_id=8453,
            round_duration_seconds=60,
            fee_rate=3000
        )
        
        round_obj = MagicMock(spec=Round)
        round_obj.round_id = "round_1"
        round_obj.job = job
        
        position = Position(tick_lower=-100, tick_upper=100, allocation0="500", allocation1="500")
        rebalance_history = [{"new_positions": [position]}]
        
        # Mock LiveExecution creation
        mock_execution = MagicMock()
        mock_execution.execution_id = "exec_123"
        self.mock_repo.create_live_execution.return_value = mock_execution
        
        with patch("requests.post") as mock_post:
            # Mock successful response
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"tx_hash": "0xabc123"}
            mock_response.text = ""
            
            mock_post.return_value = mock_response
            
            result = await self.orchestrator._execute_strategy_onchain(
                job=job,
                round_obj=round_obj,
                miner_uid=0,
                rebalance_history=rebalance_history
            )

            print(result)
            
            assert result["success"] is True
            assert result["tx_hash"] == "0xabc123"
            assert result["error"] is None
            assert result["execution_id"] == "exec_123"
            
            # Verify LiveExecution was created
            self.mock_repo.create_live_execution.assert_called_once()
            call_kwargs = self.mock_repo.create_live_execution.call_args[1]
            assert call_kwargs["round_id"] == "round_1"
            assert call_kwargs["job_id"] == "test_job"
            assert call_kwargs["miner_uid"] == 0
            assert call_kwargs["tx_hash"] == "0xabc123"

    async def test_execute_strategy_onchain_failure(self):
        """Test failed execution via executor bot."""
        job = Job(
            job_id="test_job",
            sn_liquidity_manager_address="0x123",
            pair_address="0x456",
            chain_id=8453,
            round_duration_seconds=60,
            fee_rate=3000
        )
        
        round_obj = MagicMock(spec=Round)
        round_obj.round_id = "round_1"
        round_obj.job = job
        
        position = Position(tick_lower=-100, tick_upper=100, allocation0="500", allocation1="500")
        rebalance_history = [{"new_positions": [position]}]
        
        # Mock LiveExecution creation
        mock_execution = MagicMock()
        mock_execution.execution_id = "exec_456"
        mock_execution.tx_status = None
        mock_execution.save = AsyncMock()
        self.mock_repo.create_live_execution.return_value = mock_execution
        
        with patch("validator.round_orchestrator.requests.post") as mock_post:
            # Mock failed response
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.text = "Internal Server Error"
            
            mock_post.return_value = mock_response
            
            result = await self.orchestrator._execute_strategy_onchain(
                job=job,
                round_obj=round_obj,
                miner_uid=0,
                rebalance_history=rebalance_history
            )
            
            assert result["success"] is False
            assert result["tx_hash"] is None
            assert "Executor bot returned status 500" in result["error"]
            assert result["execution_id"] == "exec_456"  # Should still create execution record
            
            # Verify LiveExecution was created with failed status
            self.mock_repo.create_live_execution.assert_called_once()
            # Verify execution status was updated to failed
            assert mock_execution.tx_status == "failed"
            mock_execution.save.assert_called_once()

    async def test_execute_strategy_onchain_network_error(self):
        """Test network error during execution."""
        job = Job(
            job_id="test_job",
            sn_liquidity_manager_address="0x123",
            pair_address="0x456",
            chain_id=8453,
            round_duration_seconds=60,
            fee_rate=3000
        )
        
        round_obj = MagicMock(spec=Round)
        round_obj.round_id = "round_1"
        round_obj.job = job
        
        position = Position(tick_lower=-100, tick_upper=100, allocation0="500", allocation1="500")
        rebalance_history = [{"new_positions": [position]}]
        
        # Mock LiveExecution creation
        mock_execution = MagicMock()
        mock_execution.execution_id = "exec_789"
        mock_execution.tx_status = None
        mock_execution.save = AsyncMock()
        self.mock_repo.create_live_execution.return_value = mock_execution
        
        with patch("requests.post") as mock_post:
            # Mock network error
            mock_post.side_effect = requests.RequestException("Connection failed")
            
            result = await self.orchestrator._execute_strategy_onchain(
                job=job,
                round_obj=round_obj,
                miner_uid=0,
                rebalance_history=rebalance_history
            )
            
            assert result["success"] is False
            assert result["tx_hash"] is None
            assert "HTTP client error" in result["error"]
            assert result["execution_id"] == "exec_789"  # Should still create execution record
            # Verify execution status was updated to failed
            assert mock_execution.tx_status == "failed"
            mock_execution.save.assert_called_once()

if __name__ == "__main__":
    unittest.main()

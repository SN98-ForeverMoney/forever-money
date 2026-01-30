"""
Full-flow test: miner + validator orchestration with mocked chain/pool.

Uses shared fixtures from conftest (wallets, mock metagraph, miner process)
and a class-based layout with setup via fixtures.
"""
from __future__ import annotations

import logging
import unittest.mock as mock
from datetime import datetime, timedelta, timezone

import pytest
from tortoise import Tortoise
import bittensor as bt
from protocol import Inventory, Position
from validator.models.job import Job, Prediction, Round
from validator.repositories.job import JobRepository
from validator.round_orchestrator import AsyncRoundOrchestrator

from tests.common import ensure_project_root

ensure_project_root()

logger = logging.getLogger(__name__)


@pytest.fixture
async def full_flow_env(
    validator_wallet,
    miner_wallet,
    mock_metagraph_with_miner,
    miner_process,
):
    """
    Set up DB, test job, mocks, and orchestrator for full-flow test.
    Depends on miner_process so miner is running before we use it.
    Yields (orchestrator, job). Tears down Tortoise after test.
    """
    metagraph = mock_metagraph_with_miner
    dendrite = bt.Dendrite(wallet=validator_wallet)

    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={"models": ["validator.models.job"]},
    )
    await Tortoise.generate_schemas(safe=True)
    job_repo = JobRepository()

    job = await Job.create(
        job_id="test_job_1",
        sn_liquidity_manager_address="0x123",
        pair_address="0x456",
        chain_id=8453,
        fee_rate=0.003,
        round_duration_seconds=60,
    )

    with (
        mock.patch("validator.round_orchestrator.SnLiqManagerService") as MockLiq,
        mock.patch("validator.round_orchestrator.PoolDataDB") as MockDB,
        mock.patch("validator.round_orchestrator.AsyncWeb3Helper") as MockWeb3,
    ):
        liq_instance = MockLiq.return_value
        liq_instance.get_inventory = mock.AsyncMock(
            return_value=Inventory(amount0="1000000000000000000", amount1="1000000000000000000")
        )
        liq_instance.get_current_positions = mock.AsyncMock(return_value=[])
        liq_instance.get_current_price = mock.AsyncMock(
            return_value=79228162514264337593543950336
        )

        db_instance = MockDB.return_value
        db_instance.get_swap_events = mock.AsyncMock(
            return_value=[
                {
                    "evt_block_number": 250,
                    "sqrt_price_x96": 79228162514264337593543950336,
                    "amount0": 1000,
                    "amount1": -1000,
                    "liquidity": 1000000,
                    "tick": 0,
                }
            ]
        )
        db_instance.get_sqrt_price_at_block = mock.AsyncMock(
            return_value=79228162514264337593543950336
        )

        config = {"executor_bot_url": None, "rebalance_check_interval": 10}
        orchestrator = AsyncRoundOrchestrator(
            job_repository=job_repo,
            dendrite=dendrite,
            metagraph=metagraph,
            config=config,
        )
        orchestrator._get_latest_block = mock.AsyncMock(
            side_effect=[200, 210, 300] + [300] * 100
        )

        job.round_duration_seconds = 60
        await job.save()
        await orchestrator._initialize_round_numbers(job)

        try:
            yield orchestrator, job
        finally:
            pass

    if Tortoise._inited:
        await Tortoise.close_connections()


class TestFullFlow:
    """Full-flow integration test: miner running, validator runs evaluation round."""

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_full_flow(self, full_flow_env):
        """Run evaluation round against live miner; assert round completes and winner set."""
        orchestrator, job = full_flow_env

        start_dt = datetime.now(timezone.utc)

        def dt_side_effect(tz=None):
            nonlocal start_dt
            start_dt += timedelta(seconds=15)
            return start_dt

        with mock.patch("validator.orchestrator.round_loops.datetime") as mock_dt:
            mock_dt.now.side_effect = dt_side_effect
            mock_dt.timezone = timezone
            await orchestrator.run_evaluation_round(job)

        rounds = await Round.filter(job=job).all()
        assert len(rounds) == 1
        r = rounds[0]
        assert r.status == "completed"

        predictions = await Prediction.filter(round=r).all()
        assert len(predictions) >= 1

        logger.info(f"Round completed successfully! Winner: {r.winner_uid}")
        assert r.winner_uid == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "slow"])

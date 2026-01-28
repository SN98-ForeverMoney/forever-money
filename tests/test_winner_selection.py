"""
Tests for per-job winner selection, tie-breaking, and scoring helpers.

Covers:
- _select_winner (orchestrator): one winner per job, tie-break by historic combined_score
- get_historic_combined_scores (JobRepository)
- get_eligible_miners / get_top_miners_by_job tie-break ordering
- Scorer.rank_miners_by_score_and_history (see also test_scorer.py)

Run:
  python -m unittest tests.test_winner_selection -v
  python -m pytest tests/test_winner_selection.py -v

DB-backed tests (TestHistoric*, TestEligible*, TestTop*) require Postgres
(JOBS_POSTGRES_* in env). Unit tests (TestSelectWinner, TestRankMiners*) run without DB.
"""
import unittest
import asyncio
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timezone
from decimal import Decimal

from tortoise import Tortoise

from validator.round_orchestrator import AsyncRoundOrchestrator
from validator.models.job import Job, MinerScore, RoundType
from validator.repositories.job import JobRepository
from validator.services.scorer import Scorer
from validator.utils.env import (
    JOBS_POSTGRES_HOST,
    JOBS_POSTGRES_PORT,
    JOBS_POSTGRES_DB,
    JOBS_POSTGRES_USER,
    JOBS_POSTGRES_PASSWORD,
)


# --- Unit tests: _select_winner (mocked repo) ---


class TestSelectWinner(unittest.IsolatedAsyncioTestCase):
    """Unit tests for _select_winner with mocked JobRepository."""

    async def asyncSetUp(self):
        self.mock_repo = AsyncMock(spec=JobRepository)
        self.mock_dendrite = AsyncMock()
        self.mock_metagraph = MagicMock()
        self.mock_metagraph.hotkeys = ["h0", "h1", "h2"]
        self.config = {"rebalance_check_interval": 100}

        self._pool_patcher = patch("validator.round_orchestrator.PoolDataDB")
        self._pool_patcher.start()
        self.addCleanup(self._pool_patcher.stop)

        self.orchestrator = AsyncRoundOrchestrator(
            self.mock_repo,
            self.mock_dendrite,
            self.mock_metagraph,
            self.config,
        )

    async def test_select_winner_empty_scores(self):
        winner = await self.orchestrator._select_winner("job1", {})
        self.assertIsNone(winner)
        self.mock_repo.get_historic_combined_scores.assert_not_called()

    async def test_select_winner_single_miner(self):
        scores = {
            1: {"hotkey": "h1", "score": 10.0},
        }
        self.mock_repo.get_historic_combined_scores.return_value = {1: 0.5}

        winner = await self.orchestrator._select_winner("job1", scores)

        self.assertIsNotNone(winner)
        self.assertEqual(winner["miner_uid"], 1)
        self.assertEqual(winner["score"], 10.0)
        self.assertEqual(winner["hotkey"], "h1")
        self.mock_repo.get_historic_combined_scores.assert_called_once_with(
            "job1", [1]
        )

    async def test_select_winner_no_tie_highest_round_score_wins(self):
        scores = {
            1: {"hotkey": "h1", "score": 5.0},
            2: {"hotkey": "h2", "score": 20.0},
            3: {"hotkey": "h3", "score": 10.0},
        }
        self.mock_repo.get_historic_combined_scores.return_value = {
            1: 0.9,
            2: 0.1,
            3: 0.5,
        }

        winner = await self.orchestrator._select_winner("job1", scores)

        self.assertIsNotNone(winner)
        self.assertEqual(winner["miner_uid"], 2)
        self.assertEqual(winner["score"], 20.0)

    async def test_select_winner_tie_break_by_historic(self):
        scores = {
            1: {"hotkey": "h1", "score": 10.0},
            2: {"hotkey": "h2", "score": 10.0},
            3: {"hotkey": "h3", "score": 10.0},
        }
        self.mock_repo.get_historic_combined_scores.return_value = {
            1: 0.3,
            2: 0.8,
            3: 0.5,
        }

        winner = await self.orchestrator._select_winner("job1", scores)

        self.assertIsNotNone(winner)
        self.assertEqual(winner["miner_uid"], 2)
        self.assertEqual(winner["score"], 10.0)

    async def test_select_winner_tie_break_missing_historic_uses_zero(self):
        scores = {
            1: {"hotkey": "h1", "score": 10.0},
            2: {"hotkey": "h2", "score": 10.0},
        }
        self.mock_repo.get_historic_combined_scores.return_value = {
            1: 0.5,
            # 2 missing -> 0.0
        }

        winner = await self.orchestrator._select_winner("job1", scores)

        self.assertIsNotNone(winner)
        self.assertEqual(winner["miner_uid"], 1)


# --- Integration tests: JobRepository (real DB) ---


class TestHistoricCombinedScores(unittest.IsolatedAsyncioTestCase):
    """Integration tests for get_historic_combined_scores with real DB."""

    async def asyncSetUp(self):
        db_url = (
            f"postgres://{JOBS_POSTGRES_USER}:{JOBS_POSTGRES_PASSWORD}@"
            f"{JOBS_POSTGRES_HOST}:{JOBS_POSTGRES_PORT}/{JOBS_POSTGRES_DB}"
        )
        await Tortoise.init(
            db_url=db_url,
            modules={"models": ["validator.models.job", "validator.models.pool_events"]},
        )
        await Tortoise.generate_schemas(safe=True)
        self.repo = JobRepository()
        self.job = await Job.create(
            job_id=f"test_historic_{int(datetime.now(timezone.utc).timestamp())}",
            sn_liquidity_manager_address="0xabc",
            pair_address="0xdef",
            chain_id=8453,
            round_duration_seconds=60,
            fee_rate=3000,
        )

    async def asyncTearDown(self):
        if Tortoise._inited:
            await Tortoise.close_connections()

    async def test_get_historic_combined_scores_empty_uids(self):
        out = await self.repo.get_historic_combined_scores(self.job.job_id, [])
        self.assertEqual(out, {})

    async def test_get_historic_combined_scores_no_miner_scores(self):
        out = await self.repo.get_historic_combined_scores(
            self.job.job_id, [99, 100]
        )
        self.assertEqual(out, {})

    async def test_get_historic_combined_scores_returns_correct_map(self):
        await MinerScore.create(
            job=self.job,
            miner_uid=1,
            miner_hotkey="hk1",
            evaluation_score=Decimal("0.6"),
            live_score=Decimal("0.2"),
            combined_score=Decimal("0.44"),
        )
        await MinerScore.create(
            job=self.job,
            miner_uid=2,
            miner_hotkey="hk2",
            evaluation_score=Decimal("0.4"),
            live_score=Decimal("0.6"),
            combined_score=Decimal("0.48"),
        )

        out = await self.repo.get_historic_combined_scores(
            self.job.job_id, [1, 2, 3]
        )

        self.assertEqual(out[1], 0.44)
        self.assertEqual(out[2], 0.48)
        self.assertNotIn(3, out)


class TestEligibleMinersTieBreak(unittest.IsolatedAsyncioTestCase):
    """Test get_eligible_miners tie-break ordering."""

    async def asyncSetUp(self):
        db_url = (
            f"postgres://{JOBS_POSTGRES_USER}:{JOBS_POSTGRES_PASSWORD}@"
            f"{JOBS_POSTGRES_HOST}:{JOBS_POSTGRES_PORT}/{JOBS_POSTGRES_DB}"
        )
        await Tortoise.init(
            db_url=db_url,
            modules={"models": ["validator.models.job", "validator.models.pool_events"]},
        )
        await Tortoise.generate_schemas(safe=True)
        self.repo = JobRepository()
        self.job = await Job.create(
            job_id=f"test_eligible_{int(datetime.now(timezone.utc).timestamp())}",
            sn_liquidity_manager_address="0xaaa",
            pair_address="0xbbb",
            chain_id=8453,
            round_duration_seconds=60,
            fee_rate=3000,
        )

    async def asyncTearDown(self):
        if Tortoise._inited:
            await Tortoise.close_connections()

    async def test_eligible_miners_tie_break_by_evaluations_then_live(self):
        for uid, evals, live in [(1, 5, 2), (2, 10, 1), (3, 5, 5)]:
            await MinerScore.create(
                job=self.job,
                miner_uid=uid,
                miner_hotkey=f"hk{uid}",
                evaluation_score=Decimal("0.5"),
                live_score=Decimal("0.5"),
                combined_score=Decimal("0.5"),
                is_eligible_for_live=True,
                participation_days=7,
                total_evaluations=evals,
                total_live_rounds=live,
            )

        miners = await self.repo.get_eligible_miners(self.job.job_id, min_score=0.0)

        self.assertEqual(len(miners), 3)
        # Same combined_score: order by total_evaluations desc, then total_live_rounds desc
        self.assertEqual(miners[0].miner_uid, 2)  # 10 evals
        self.assertEqual(miners[1].miner_uid, 3)  # 5 evals, 5 live
        self.assertEqual(miners[2].miner_uid, 1)  # 5 evals, 2 live


class TestTopMinersByJobTieBreak(unittest.IsolatedAsyncioTestCase):
    """Test get_top_miners_by_job tie-break (one winner per job)."""

    async def asyncSetUp(self):
        db_url = (
            f"postgres://{JOBS_POSTGRES_USER}:{JOBS_POSTGRES_PASSWORD}@"
            f"{JOBS_POSTGRES_HOST}:{JOBS_POSTGRES_PORT}/{JOBS_POSTGRES_DB}"
        )
        await Tortoise.init(
            db_url=db_url,
            modules={"models": ["validator.models.job", "validator.models.pool_events"]},
        )
        await Tortoise.generate_schemas(safe=True)
        self.repo = JobRepository()
        self.job = await Job.create(
            job_id=f"test_top_{int(datetime.now(timezone.utc).timestamp())}",
            sn_liquidity_manager_address="0xccc",
            pair_address="0xddd",
            chain_id=8453,
            round_duration_seconds=60,
            fee_rate=3000,
        )

    async def asyncTearDown(self):
        if Tortoise._inited:
            await Tortoise.close_connections()

    async def test_top_miners_by_job_tie_break(self):
        await MinerScore.create(
            job=self.job,
            miner_uid=10,
            miner_hotkey="hk10",
            evaluation_score=Decimal("0.5"),
            live_score=Decimal("0.5"),
            combined_score=Decimal("0.5"),
            total_evaluations=3,
            total_live_rounds=1,
        )
        await MinerScore.create(
            job=self.job,
            miner_uid=20,
            miner_hotkey="hk20",
            evaluation_score=Decimal("0.5"),
            live_score=Decimal("0.5"),
            combined_score=Decimal("0.5"),
            total_evaluations=5,
            total_live_rounds=2,
        )

        top = await self.repo.get_top_miners_by_job()

        self.assertIn(self.job.job_id, top)
        self.assertEqual(top[self.job.job_id], 20)  # more evals + live


# --- Scorer.rank_miners_by_score_and_history (keep alongside above) ---


class TestRankMinersByScoreAndHistory(unittest.TestCase):
    """Mirror of test_scorer rank_miners tests; runnable via unittest."""

    def test_no_tie(self):
        round_scores = {1: 10.0, 2: 20.0, 3: 5.0}
        historic = {1: 0.5, 2: 0.3, 3: 0.8}
        ranked = Scorer.rank_miners_by_score_and_history(round_scores, historic)
        self.assertEqual(ranked[0][0], 2)
        self.assertEqual(ranked[0][1], 20.0)
        self.assertEqual([r[0] for r in ranked], [2, 1, 3])

    def test_tie_break(self):
        round_scores = {1: 10.0, 2: 10.0, 3: 10.0}
        historic = {1: 0.3, 2: 0.8, 3: 0.5}
        ranked = Scorer.rank_miners_by_score_and_history(round_scores, historic)
        self.assertEqual([r[0] for r in ranked], [2, 3, 1])

    def test_missing_historic(self):
        round_scores = {1: 10.0, 2: 10.0}
        historic = {1: 0.5}
        ranked = Scorer.rank_miners_by_score_and_history(round_scores, historic)
        self.assertEqual(ranked[0][0], 1)
        self.assertEqual(len(ranked), 2)


if __name__ == "__main__":
    unittest.main()

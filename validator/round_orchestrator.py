"""
Async Round Orchestrator for SN98 ForeverMoney Validator.

Fully async implementation using:
- Tortoise ORM for database
- RebalanceQuery-only protocol (no StrategyRequest)
- Validator-generated initial positions

Orchestration logic is split across validator.orchestrator:
- round_loops: evaluation and live block loops
- winner: winner selection and tie-breaking
- miner_query: query miners for rebalance decisions
- executor: execute strategy on-chain via executor bot
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

import bittensor as bt

from protocol.models import Inventory, Position
from validator.models.job import Job, Round, RoundType
from validator.repositories.job import JobRepository
from validator.repositories.pool import PoolDataDB
from validator.services.backtester import BacktesterService
from validator.services.liqmanager import SnLiqManagerService
from validator.utils.web3 import AsyncWeb3Helper

from validator.orchestrator.round_loops import (
    run_with_miner_for_live,
    run_with_miners_batch_for_evaluation,
)
from validator.orchestrator.winner import select_winner

logger = logging.getLogger(__name__)

# Max miners to evaluate concurrently per batch (avoids overload with many miners)
EVALUATION_BATCH_SIZE = 51


class AsyncRoundOrchestrator:
    """
    Orchestrates evaluation and live rounds for multiple jobs concurrently.

    All operations are async.
    """

    def __init__(
        self,
        job_repository: JobRepository,
        dendrite: bt.Dendrite,
        metagraph: bt.Metagraph,
        config: Dict,
    ):
        self.job_repository = job_repository
        self.dendrite = dendrite
        self.metagraph = metagraph
        self.config = config
        self.round_numbers: Dict[str, Dict[str, int]] = {}
        self.rebalance_check_interval = config.get("rebalance_check_interval", 100)
        self.backtester = BacktesterService(PoolDataDB())

    async def _initialize_round_numbers(self, job: Job) -> None:
        """Initialize round numbers from database for a job."""
        eval_round = await Round.filter(
            job=job, round_type=RoundType.EVALUATION
        ).order_by("-round_number").first()
        live_round = await Round.filter(
            job=job, round_type=RoundType.LIVE
        ).order_by("-round_number").first()
        self.round_numbers[job.job_id] = {
            "evaluation": eval_round.round_number if eval_round else 0,
            "live": live_round.round_number if live_round else 0,
        }
        logger.info(
            f"Initialized round numbers for job {job.job_id}: "
            f"evaluation={self.round_numbers[job.job_id]['evaluation']}, "
            f"live={self.round_numbers[job.job_id]['live']}"
        )

    async def _get_latest_block(self, chain_id: int) -> int:
        """Get latest block from chain."""
        w3 = AsyncWeb3Helper.make_web3(chain_id)
        return await w3.web3.eth.block_number

    async def run_job_continuously(self, job: Job) -> None:
        """Run a job continuously with dual-mode rounds."""
        logger.info(f"Starting continuous operation for job {job.job_id}")
        if job.job_id not in self.round_numbers:
            await self._initialize_round_numbers(job)
        while True:
            try:
                await asyncio.gather(
                    self.run_evaluation_round(job),
                    self.run_live_round(job),
                )
                logger.info(
                    f"Job {job.job_id}: Sleeping for {job.round_duration_seconds} s"
                )
                await asyncio.sleep(job.round_duration_seconds)
            except Exception as e:
                logger.error(f"Error in job {job.job_id}: {e}", exc_info=True)
                await asyncio.sleep(job.round_duration_seconds)

    async def run_evaluation_round(self, job: Job) -> None:
        """Run an evaluation round: backtest miners, score, select winner."""
        liq_manager = SnLiqManagerService(
            job.chain_id,
            job.sn_liquidity_manager_address,
            job.pair_address,
        )
        my_uid = self.config.get("my_uid")
        active_uids = [
            uid
            for uid in range(len(self.metagraph.S))
            if my_uid is None or uid != my_uid
        ]
        if not active_uids:
            logger.warning("No active miners found.")
            return

        self.round_numbers[job.job_id]["evaluation"] += 1
        round_number = self.round_numbers[job.job_id]["evaluation"]
        current_block = await self._get_latest_block(job.chain_id)
        round_obj = await self.job_repository.create_round(
            job=job,
            round_type=RoundType.EVALUATION,
            round_number=round_number,
            start_block=current_block,
        )
        self.round_numbers[job.job_id]["evaluation"] = round_obj.round_number
        round_number = round_obj.round_number
        logger.info("=" * 60)
        logger.info(f"Starting EVALUATION round #{round_number} for job {job.job_id}")
        logger.info("=" * 60)
        inventory = await liq_manager.get_inventory()
        initial_positions = await liq_manager.get_current_positions()
        logger.info(f"Loaded {len(initial_positions)} initial positions from on-chain")

        scores = await self._evaluate_miners(
            job=job,
            round_=round_obj,
            active_uids=active_uids,
            initial_positions=initial_positions,
            start_block=current_block,
            inventory=inventory,
            liq_manager=liq_manager,
        )

        winner = await select_winner(self.job_repository, job.job_id, scores)
        if winner:
            logger.info(
                f"Winner (evaluation round #{round_number}, job {job.job_id}): "
                f"Miner UID={winner['miner_uid']}, score={winner['score']:.4f}, "
                f"hotkey={winner['hotkey']}"
            )
        else:
            logger.warning(f"No winner for evaluation round {round_number}")

        await self.job_repository.complete_round(
            round_id=round_obj.round_id,
            winner_uid=winner["miner_uid"] if winner else None,
            performance_data={"scores": {str(k): v["score"] for k, v in scores.items()}},
        )
        # Run score + participation updates in parallel batches to reduce DB latency
        job_id = job.job_id
        items = list(scores.items())

        async def _update_one(uid: int, data: dict) -> None:
            accepted = data["accepted"]
            await self.job_repository.update_miner_score(
                job_id=job_id,
                miner_uid=uid,
                miner_hotkey=data["hotkey"],
                evaluation_score=data["score"],
                round_type=RoundType.EVALUATION,
                accepted=accepted,
            )
            await self.job_repository.update_miner_participation(
                job_id=job_id, miner_uid=uid, accepted=accepted
            )

        for i in range(0, len(items), EVALUATION_BATCH_SIZE):
            batch = items[i : i + EVALUATION_BATCH_SIZE]
            await asyncio.gather(
                *[_update_one(uid, data) for uid, data in batch]
            )
        logger.info(f"Completed evaluation round {round_number}")

    async def _select_winner(
        self, job_id: str, scores: Dict[int, Dict]
    ) -> Optional[Dict]:
        """Select one winner per job; tie-break by historic combined_score. For tests."""
        return await select_winner(self.job_repository, job_id, scores)

    async def run_live_round(self, job: Job) -> None:
        """Run a live round with the first eligible miner from evaluation ranking."""
        ranking = await self.job_repository.get_evaluation_round_ranking(
            job.job_id
        )
        if not ranking:
            logger.info(
                f"No evaluation ranking for job {job.job_id}, skipping live round"
            )
            return
        eligible_uids = {
            s.miner_uid
            for s in await self.job_repository.get_eligible_miners(job.job_id)
        }
        winner_uid = None
        for uid in ranking:
            if uid in eligible_uids:
                winner_uid = uid
                break
        if winner_uid is None:
            logger.info(
                f"No eligible miners for live round (tried: {ranking}), skipping"
            )
            return

        self.round_numbers[job.job_id]["live"] += 1
        round_number = self.round_numbers[job.job_id]["live"]
        current_block = await self._get_latest_block(job.chain_id)
        round_obj = await self.job_repository.create_round(
            job=job,
            round_type=RoundType.LIVE,
            round_number=round_number,
            start_block=current_block,
        )
        self.round_numbers[job.job_id]["live"] = round_obj.round_number
        round_number = round_obj.round_number
        logger.info("=" * 60)
        logger.info(
            f"Winner for live execution (job {job.job_id}, round #{round_number}): "
            f"Miner UID={winner_uid}, hotkey={self.metagraph.hotkeys[winner_uid]}"
        )
        logger.info(
            f"Starting LIVE round #{round_number} for job {job.job_id} with Miner {winner_uid}"
        )
        logger.info("=" * 60)
        liq_manager = SnLiqManagerService(
            job.chain_id,
            job.sn_liquidity_manager_address,
            job.pair_address,
        )
        inventory = await liq_manager.get_inventory()
        initial_positions = await liq_manager.get_current_positions()

        result = await run_with_miner_for_live(
            miner_uid=winner_uid,
            job=job,
            round_=round_obj,
            initial_positions=initial_positions,
            start_block=current_block,
            initial_inventory=inventory,
            rebalance_check_interval=self.rebalance_check_interval,
            liq_manager=liq_manager,
            job_repository=self.job_repository,
            config=self.config,
            dendrite=self.dendrite,
            metagraph=self.metagraph,
            backtester=self.backtester,
            get_block_fn=self._get_latest_block,
        )

        if result["accepted"]:
            execution_failures = result.get("execution_failures", 0)
            execution_results = result.get("execution_results", [])
            total_executions = len(execution_results)
            rebalance_history = result.get("rebalance_history", [])
            logger.info(
                f"Live execution summary (job {job.job_id}, round #{round_number}, "
                f"winner Miner {winner_uid}): {len(rebalance_history)} rebalance(s), "
                f"{total_executions - execution_failures}/{total_executions} on-chain execution(s) succeeded, "
                f"score={result.get('score', 0):.4f}"
            )
            if rebalance_history:
                for i, step in enumerate(rebalance_history):
                    new_pos = step.get("new_positions") or []
                    n_pos = len(new_pos)
                    pos_desc = []
                    for p in new_pos[:5]:  # log up to 5 positions
                        if hasattr(p, "tick_lower"):
                            pos_desc.append(
                                f"[tick_{p.tick_lower}_{p.tick_upper} "
                                f"a0={getattr(p, 'allocation0', '?')} a1={getattr(p, 'allocation1', '?')}]"
                            )
                        else:
                            pos_desc.append(str(p)[:80])
                    if len(new_pos) > 5:
                        pos_desc.append(f"...+{len(new_pos) - 5} more")
                    logger.info(
                        f"  Live strategy step {i + 1}: {n_pos} position(s) "
                        f"block={step.get('block')} tx_hash={step.get('tx_hash') or 'N/A'} "
                        f"positions={', '.join(pos_desc) if pos_desc else 'none'}"
                    )
            if total_executions > 0 and execution_failures == total_executions:
                logger.error(
                    f"All {total_executions} executions failed for miner {winner_uid} "
                    f"in live round {round_number}. Not updating score."
                )
            else:
                live_score = result["score"]
                if execution_failures > 0:
                    logger.warning(
                        f"Miner {winner_uid} had {execution_failures}/{total_executions} "
                        f"execution failures in live round {round_number}. Score may be inaccurate."
                    )
                logger.info(f"Miner {winner_uid} live score: {live_score}")
                await self.job_repository.update_miner_score(
                    job_id=job.job_id,
                    miner_uid=winner_uid,
                    miner_hotkey=self.metagraph.hotkeys[winner_uid],
                    live_score=live_score,
                    round_type=RoundType.LIVE,
                    accepted=True,
                )
            await self.job_repository.save_rebalance_decision(
                round_id=round_obj.round_id,
                job_id=job.job_id,
                miner_uid=winner_uid,
                miner_hotkey=self.metagraph.hotkeys[winner_uid],
                accepted=True,
                rebalance_data=result["rebalance_history"],
                refusal_reason=None,
                response_time_ms=result.get("total_query_time_ms", 0),
            )
        else:
            logger.warning(
                f"Miner {winner_uid} failed/refused live round: {result.get('refusal_reason')}"
            )

        await self.job_repository.complete_round(
            round_id=round_obj.round_id,
            winner_uid=winner_uid if result["accepted"] else None,
            performance_data={"score": result.get("score", 0)},
        )
        logger.info(f"Completed LIVE round {round_number}")

    async def _evaluate_miners(
        self,
        job: Job,
        round_: Round,
        active_uids: List[int],
        initial_positions: List[Position],
        start_block: int,
        inventory: Inventory,
        liq_manager,
    ) -> Dict[int, Dict]:
        """Evaluate all active miners via batched dendrite calls (up to EVALUATION_BATCH_SIZE per batch)."""
        results = await run_with_miners_batch_for_evaluation(
            miner_uids=active_uids,
            job=job,
            round_=round_,
            initial_positions=initial_positions,
            start_block=start_block,
            initial_inventory=inventory,
            rebalance_check_interval=self.rebalance_check_interval,
            liq_manager=liq_manager,
            job_repository=self.job_repository,
            dendrite=self.dendrite,
            metagraph=self.metagraph,
            backtester=self.backtester,
            get_block_fn=self._get_latest_block,
            query_batch_size=EVALUATION_BATCH_SIZE,
        )
        scores: Dict[int, Dict] = {}
        for uid, res in results.items():
            score_val = res["score"] if res["accepted"] else 0.0
            scores[uid] = {
                "hotkey": self.metagraph.hotkeys[uid],
                "score": score_val,
                "accepted": res["accepted"],
                "result": res,
            }

        round_id = round_.round_id
        job_id = job.job_id

        async def _save_one(uid: int, res: dict) -> None:
            if res["accepted"]:
                await self.job_repository.save_rebalance_decision(
                    round_id=round_id,
                    job_id=job_id,
                    miner_uid=uid,
                    miner_hotkey=self.metagraph.hotkeys[uid],
                    accepted=True,
                    rebalance_data=res["rebalance_history"],
                    refusal_reason=None,
                    response_time_ms=res.get("total_query_time_ms", 0),
                )
            else:
                await self.job_repository.save_rebalance_decision(
                    round_id=round_id,
                    job_id=job_id,
                    miner_uid=uid,
                    miner_hotkey=self.metagraph.hotkeys[uid],
                    accepted=False,
                    rebalance_data=None,
                    refusal_reason=res.get("refusal_reason"),
                    response_time_ms=res.get("total_query_time_ms", 0),
                )

        items = list(results.items())
        for i in range(0, len(items), EVALUATION_BATCH_SIZE):
            batch = items[i : i + EVALUATION_BATCH_SIZE]
            await asyncio.gather(
                *[_save_one(uid, res) for uid, res in batch]
            )

        return scores

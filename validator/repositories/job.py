"""
Async Job Manager for SN98 ForeverMoney Validator.

Uses Tortoise ORM for all database operations.
All methods are async.
"""
import logging
from typing import List, Optional, Dict
from datetime import datetime, timedelta, date, timezone
from decimal import Decimal

from validator.models.job import (
    Job,
    Round,
    Prediction,
    MinerScore,
    MinerParticipation,
    LiveExecution,
    RoundType,
    RoundStatus,
)

logger = logging.getLogger(__name__)


class JobRepository:
    """Async job manager using Tortoise ORM."""

    async def get_active_jobs(self) -> List[Job]:
        """
        Get all active jobs from the database.

        Returns:
            List of active Job objects
        """
        jobs = await Job.filter(is_active=True).order_by("created_at")
        logger.info(f"Found {len(jobs)} active jobs")
        return jobs

    async def create_round(
        self,
        job: Job,
        round_type: RoundType,
        round_number: int,
        start_block: int,
    ) -> Round:
        """
        Create a new round for a job or return existing one.

        Args:
            job: Job to create round for
            round_type: RoundType.EVALUATION or RoundType.LIVE
            round_number: Sequential round number
            start_block: Start block for the round
        Returns:
            Created Round object
        """
        start_time = datetime.now(timezone.utc)
        round_deadline = start_time + timedelta(seconds=job.round_duration_seconds)
        round_id = f"{job.job_id}_{round_type.value}_{round_number}_{int(start_time.timestamp())}"

        round_obj = await Round.create(
            round_id=round_id,
            job=job,
            round_type=round_type,
            round_number=round_number,
            start_time=start_time,
            round_deadline=round_deadline,
            start_block=start_block,
            status=RoundStatus.ACTIVE,
        )

        logger.info(f"Created {round_type.value} round: {round_id}")
        return round_obj

    async def save_rebalance_decision(
        self,
        round_id: str,
        job_id: str,
        miner_uid: int,
        miner_hotkey: str,
        accepted: bool,
        rebalance_data: Optional[List[Dict]],
        refusal_reason: Optional[str],
        response_time_ms: int,
    ) -> str:
        """
        Save a miner's rebalancing decision to the database.

        Args:
            round_id: Round identifier
            job_id: Job identifier
            miner_uid: Miner UID
            miner_hotkey: Miner hotkey
            accepted: Whether miner accepted the job
            rebalance_data: List of rebalancing decisions
            refusal_reason: Reason for refusal (if declined)
            response_time_ms: Response time in milliseconds

        Returns:
            Prediction ID
        """
        prediction_id = f"{round_id}_{miner_uid}"

        # Get job and round objects
        job = await Job.get(job_id=job_id)
        round_obj = await Round.get(round_id=round_id)

        # Serialize rebalance_data to make it JSON-serializable
        # Convert Inventory and Position objects to dicts
        serialized_data = self._serialize_rebalance_data(rebalance_data)

        # Upsert prediction
        prediction, created = await Prediction.update_or_create(
            round=round_obj,
            miner_uid=miner_uid,
            defaults={
                "prediction_id": prediction_id,
                "job": job,
                "miner_hotkey": miner_hotkey,
                "accepted": accepted,
                "refusal_reason": refusal_reason,
                "response_time_ms": response_time_ms,
                "prediction_data": serialized_data,
            },
        )

        logger.debug(f"Saved decision for miner {miner_uid} in round {round_id}")
        return prediction_id

    def _serialize_rebalance_data(self, rebalance_data: Optional[List[Dict]]) -> Optional[List[Dict]]:
        """
        Serialize rebalance_data to make it JSON-serializable.
        Converts Inventory and Position objects to dictionaries.

        Args:
            rebalance_data: List of rebalancing decision dictionaries

        Returns:
            Serialized list of dictionaries
        """
        if rebalance_data is None:
            return None

        serialized = []
        for item in rebalance_data:
            if not isinstance(item, dict):
                continue

            serialized_item = {}
            for key, value in item.items():
                # Handle Inventory objects
                if hasattr(value, 'amount0') and hasattr(value, 'amount1'):
                    # It's an Inventory object (Pydantic model)
                    serialized_item[key] = {
                        "amount0": str(value.amount0),
                        "amount1": str(value.amount1)
                    }
                # Handle lists of Position objects
                elif isinstance(value, list) and len(value) > 0:
                    if hasattr(value[0], 'tick_lower') and hasattr(value[0], 'tick_upper'):
                        # It's a list of Position objects
                        serialized_item[key] = [
                            {
                                "tick_lower": pos.tick_lower,
                                "tick_upper": pos.tick_upper,
                                "allocation0": str(pos.allocation0),
                                "allocation1": str(pos.allocation1),
                                **({"confidence": pos.confidence} if hasattr(pos, 'confidence') and pos.confidence is not None else {})
                            }
                            for pos in value
                        ]
                    else:
                        # Regular list, keep as is
                        serialized_item[key] = value
                # Handle datetime objects
                elif hasattr(value, 'isoformat'):
                    # datetime or date object
                    serialized_item[key] = value.isoformat()
                else:
                    # Regular value (str, int, float, dict, etc.)
                    serialized_item[key] = value

            serialized.append(serialized_item)

        return serialized

    async def get_round_predictions(self, round_id: str) -> List[Prediction]:
        """
        Get all accepted predictions for a round.

        Args:
            round_id: Round identifier

        Returns:
            List of Prediction objects
        """
        predictions = await Prediction.filter(
            round_id=round_id, accepted=True
        ).order_by("submitted_at")

        return predictions

    async def update_miner_score(
        self,
        job_id: str,
        miner_uid: int,
        miner_hotkey: str,
        evaluation_score: Optional[float] = None,
        live_score: Optional[float] = None,
        round_type: RoundType = RoundType.EVALUATION,
    ) -> MinerScore:
        """
        Update miner's reputation score for a job using EMA.

        Evaluation Score EMA: new = old * 0.9 + latest * 0.1
        Live Score EMA: new = old * 0.7 + latest * 0.3
        Combined: eval * 0.6 + live * 0.4

        Args:
            job_id: Job identifier
            miner_uid: Miner UID
            miner_hotkey: Miner hotkey
            evaluation_score: New evaluation score (if applicable)
            live_score: New live score (if applicable)
            round_type: Type of round that generated this score
        """
        job = await Job.get(job_id=job_id)

        # Get or create miner score
        score, created = await MinerScore.get_or_create(
            job=job,
            miner_uid=miner_uid,
            defaults={
                "miner_hotkey": miner_hotkey,
                "evaluation_score": Decimal("0.0"),
                "live_score": Decimal("0.0"),
                "combined_score": Decimal("0.0"),
            },
        )

        # Update scores with EMA
        if evaluation_score is not None:
            old_eval = float(score.evaluation_score)
            new_eval = old_eval * 0.9 + evaluation_score * 0.1
            score.evaluation_score = Decimal(str(new_eval))

        if live_score is not None:
            old_live = float(score.live_score)
            new_live = old_live * 0.7 + live_score * 0.3
            score.live_score = Decimal(str(new_live))

        # Update combined score
        score.combined_score = score.evaluation_score * Decimal(
            "0.6"
        ) + score.live_score * Decimal("0.4")

        # Update counters
        if round_type == RoundType.EVALUATION:
            score.total_evaluations += 1
        elif round_type == RoundType.LIVE:
            score.total_live_rounds += 1

        await score.save()

        logger.debug(
            f"Updated score for miner {miner_uid} on job {job_id}: {float(score.combined_score):.4f}"
        )
        return score

    async def update_miner_participation(
        self, job_id: str, miner_uid: int, participated: bool
    ):
        """
        Track daily participation for a miner on a job.

        Updates:
        - Daily participation record
        - Participation days count
        - Eligibility for live mode (7+ days)

        Args:
            job_id: Job identifier
            miner_uid: Miner UID
            participated: Whether miner participated
        """
        job = await Job.get(job_id=job_id)
        today = date.today()

        # Upsert participation record
        participation, created = await MinerParticipation.update_or_create(
            job=job,
            miner_uid=miner_uid,
            participation_date=today,
            defaults={"participated": participated, "rounds_participated": 1},
        )

        if not created:
            participation.rounds_participated += 1
            await participation.save()

        # Update eligibility
        score = await MinerScore.get_or_none(job=job, miner_uid=miner_uid)
        if score:
            # Count distinct days in last 7 days
            seven_days_ago = today - timedelta(days=7)
            participation_count = await MinerParticipation.filter(
                job=job, miner_uid=miner_uid, participation_date__gte=seven_days_ago
            ).count()

            score.participation_days = participation_count
            score.is_eligible_for_live = participation_count >= 7
            await score.save()

    async def get_eligible_miners(
        self, job_id: str, min_score: float = 0.0
    ) -> List[MinerScore]:
        """
        Get miners eligible for live mode (participated for 7+ days).

        Args:
            job_id: Job identifier
            min_score: Minimum combined score threshold

        Returns:
            List of eligible MinerScore objects, sorted by score descending
        """
        job = await Job.get(job_id=job_id)

        scores = await MinerScore.filter(
            job=job,
            is_eligible_for_live=True,
            combined_score__gte=Decimal(str(min_score)),
        ).order_by("-combined_score")

        return scores

    async def complete_round(
        self, round_id: str, winner_uid: Optional[int], performance_data: Optional[Dict]
    ):
        """
        Mark a round as completed and record the winner.

        Args:
            round_id: Round identifier
            winner_uid: UID of winning miner
            performance_data: Performance metrics
        """
        round_obj = await Round.get(round_id=round_id)
        round_obj.status = RoundStatus.COMPLETED
        round_obj.end_time = datetime.now(timezone.utc)
        round_obj.winner_uid = winner_uid
        round_obj.performance_data = performance_data
        await round_obj.save()

        logger.info(f"Completed round {round_id}, winner: {winner_uid}")

    async def get_previous_winner(
        self, job_id: str, round_type: RoundType = RoundType.EVALUATION
    ) -> Optional[int]:
        """
        Get the winner of the previous round for a job.

        Args:
            job_id: Job identifier
            round_type: Type of round to look for

        Returns:
            Miner UID of previous winner, or None
        """
        job = await Job.get(job_id=job_id)

        round_obj = (
            await Round.filter(
                job=job, round_type=round_type, status=RoundStatus.COMPLETED
            )
            .order_by("-round_number")
            .first()
        )

        return round_obj.winner_uid if round_obj else None

    async def get_top_miners_by_job(self) -> Dict[str, int]:
        """
        Get the top miner UID for each active job based on combined score.
        
        Returns:
            Dict mapping job_id -> miner_uid
        """
        jobs = await Job.filter(is_active=True)
        winners = {}
        
        for job in jobs:
            # Get top score
            top_score = await MinerScore.filter(job=job).order_by("-combined_score").first()
            if top_score:
                winners[job.job_id] = top_score.miner_uid
                
        return winners

    async def create_live_execution(
        self,
        round_id: str,
        job_id: str,
        miner_uid: int,
        strategy_data: Dict,
        tx_hash: Optional[str] = None,
    ) -> LiveExecution:
        """
        Record a live execution.

        Args:
            round_id: Round identifier
            job_id: Job identifier
            miner_uid: Miner UID
            strategy_data: Strategy data
            tx_hash: Transaction hash (if executed)

        Returns:
            Created LiveExecution object
        """
        job = await Job.get(job_id=job_id)
        round_obj = await Round.get(round_id=round_id)

        execution_id = f"{round_id}_{miner_uid}_{int(datetime.now(timezone.utc).timestamp())}"

        execution = await LiveExecution.create(
            execution_id=execution_id,
            round=round_obj,
            job=job,
            miner_uid=miner_uid,
            sn_liquidity_manager_address=job.sn_liquidity_manager_address,
            strategy_data=strategy_data,
            tx_hash=tx_hash,
            tx_status="pending" if tx_hash else None,
        )

        logger.info(f"Created live execution: {execution_id}")
        return execution

"""
Tortoise ORM Models for SN98 Jobs System.

All database operations are async using Tortoise ORM.
"""
from enum import Enum
from typing import Optional

from tortoise import Tortoise, fields
from tortoise.models import Model

from validator.utils.env import (
    JOBS_POSTGRES_HOST,
    JOBS_POSTGRES_PORT,
    JOBS_POSTGRES_DB,
    JOBS_POSTGRES_USER,
    JOBS_POSTGRES_PASSWORD,
)


class RoundType(str, Enum):
    """Round type enum."""

    EVALUATION = "evaluation"
    LIVE = "live"


class RoundStatus(str, Enum):
    """Round status enum."""

    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"


class Job(Model):
    """
    Liquidity management job.

    Represents a vault managing liquidity for a specific trading pair.
    """

    id = fields.IntField(primary_key=True)
    job_id = fields.CharField(max_length=255, unique=True, db_index=True)
    sn_liquidity_manager_address = fields.CharField(max_length=42)
    pair_address = fields.CharField(max_length=42)
    fee_rate = fields.FloatField(default=0.03)
    target = fields.CharField(max_length=50, default="PoL")
    target_ratio = fields.FloatField(default=0.5)
    chain_id = fields.IntField(default=8453)
    is_active = fields.BooleanField(default=True, db_index=True)
    round_duration_seconds = fields.IntField(default=900)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)
    metadata = fields.JSONField(null=True)

    # Relations
    rounds: fields.ReverseRelation["Round"]
    miner_scores: fields.ReverseRelation["MinerScore"]

    class Meta:
        table = "jobs"

    def __str__(self):
        return f"Job({self.job_id})"


class Round(Model):
    """
    Evaluation or live round for a job.

    Each round tests miners and selects a winner.
    """

    id = fields.IntField(primary_key=True)
    round_id = fields.CharField(max_length=255, unique=True, db_index=True)
    job = fields.ForeignKeyField(
        "models.Job", related_name="rounds", on_delete=fields.CASCADE
    )
    round_type = fields.CharEnumField(RoundType, db_index=True)
    round_number = fields.IntField()
    start_time = fields.DatetimeField()
    round_deadline = fields.DatetimeField()
    end_time = fields.DatetimeField(null=True)
    winner_uid = fields.IntField(null=True)
    start_block = fields.IntField()
    status = fields.CharEnumField(RoundStatus, default=RoundStatus.PENDING, db_index=True)
    performance_data = fields.JSONField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    # Relations
    predictions: fields.ReverseRelation["Prediction"]

    class Meta:
        table = "rounds"
        unique_together = (("job_id", "round_number", "round_type"),)

    def __str__(self):
        return f"Round({self.round_id}, {self.round_type})"


class Prediction(Model):
    """
    Miner prediction/response for a round.

    Stores whether miner accepted the job and their rebalancing decisions.
    """

    id = fields.IntField(primary_key=True)
    prediction_id = fields.CharField(max_length=255, unique=True, db_index=True)
    round = fields.ForeignKeyField(
        "models.Round", related_name="predictions", on_delete=fields.CASCADE
    )
    job = fields.ForeignKeyField(
        "models.Job", related_name="predictions", on_delete=fields.CASCADE
    )
    miner_uid = fields.IntField(db_index=True)
    miner_hotkey = fields.CharField(max_length=66)
    accepted = fields.BooleanField(default=False)
    refusal_reason = fields.TextField(null=True)
    response_time_ms = fields.IntField(null=True)
    prediction_data = fields.JSONField(null=True)  # Rebalancing decisions
    submitted_at = fields.DatetimeField(auto_now_add=True)
    simulated_performance = fields.JSONField(null=True)

    class Meta:
        table = "predictions"
        unique_together = (("round_id", "miner_uid"),)
        indexes = (("job_id", "miner_uid"),)

    def __str__(self):
        return f"Prediction({self.miner_uid}, round={self.round})"


class MinerScore(Model):
    """
    Reputation-based score for a miner on a specific job.

    Tracks historical performance using exponential moving averages.
    """

    id = fields.IntField(primary_key=True)
    job = fields.ForeignKeyField(
        "models.Job", related_name="miner_scores", on_delete=fields.CASCADE
    )
    miner_uid = fields.IntField(db_index=True)
    miner_hotkey = fields.CharField(max_length=66)

    # Score components
    evaluation_score = fields.DecimalField(max_digits=10, decimal_places=6, default=0.0)
    live_score = fields.DecimalField(max_digits=10, decimal_places=6, default=0.0)
    combined_score = fields.DecimalField(
        max_digits=10, decimal_places=6, default=0.0, db_index=True
    )

    # Activity tracking
    total_evaluations = fields.IntField(default=0)
    total_live_rounds = fields.IntField(default=0)
    successful_evaluations = fields.IntField(default=0)
    successful_live_rounds = fields.IntField(default=0)
    refusals = fields.IntField(default=0)

    # Participation tracking
    first_seen = fields.DatetimeField(auto_now_add=True)
    last_active = fields.DatetimeField(auto_now=True, db_index=True)
    participation_days = fields.IntField(default=0)
    is_eligible_for_live = fields.BooleanField(default=False, db_index=True)

    # Historical data
    score_history = fields.JSONField(null=True)

    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "miner_scores"
        unique_together = (("job_id", "miner_uid"),)
        indexes = (
            ("job_id", "combined_score"),
            ("job_id", "is_eligible_for_live", "combined_score"),
        )

    def __str__(self):
        return f"MinerScore(miner={self.miner_uid}, job={self.job}, score={self.combined_score})"


class MinerParticipation(Model):
    """
    Daily participation tracking for miners.

    Used to calculate eligibility for live mode.
    """

    id = fields.IntField(primary_key=True)
    job = fields.ForeignKeyField(
        "models.Job", related_name="participations", on_delete=fields.CASCADE
    )
    miner_uid = fields.IntField()
    participation_date = fields.DateField(db_index=True)
    participated = fields.BooleanField(default=True)
    rounds_participated = fields.IntField(default=0)
    rounds_refused = fields.IntField(default=0)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "miner_participation"
        unique_together = (("job_id", "miner_uid", "participation_date"),)
        indexes = (("job_id", "miner_uid", "participation_date"),)

    def __str__(self):
        return f"Participation(miner={self.miner_uid}, job={self.job}, date={self.participation_date})"


class LiveExecution(Model):
    """
    On-chain execution record for live rounds.

    Tracks actual position placements and performance.
    """

    id = fields.IntField(primary_key=True)
    execution_id = fields.CharField(max_length=255, unique=True, db_index=True)
    round = fields.ForeignKeyField(
        "models.Round", related_name="executions", on_delete=fields.CASCADE
    )
    job = fields.ForeignKeyField(
        "models.Job", related_name="executions", on_delete=fields.CASCADE
    )
    miner_uid = fields.IntField()
    sn_liquidity_manager_address = fields.CharField(max_length=42)

    # Execution details
    strategy_data = fields.JSONField()
    tx_hash = fields.CharField(max_length=66, null=True)
    tx_status = fields.CharField(max_length=20, null=True)

    # Performance tracking
    actual_performance = fields.JSONField(null=True)

    executed_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "live_executions"
        indexes = (
            ("job_id",),
            ("round_id",),
        )

    def __str__(self):
        return f"LiveExecution({self.execution_id})"


# Tortoise ORM configuration
TORTOISE_ORM = {
    "connections": {
        "default": {
            "engine": "tortoise.backends.asyncpg",
            "credentials": {
                "host": JOBS_POSTGRES_HOST,
                "port": JOBS_POSTGRES_PORT,
                "user": JOBS_POSTGRES_USER,
                "password": JOBS_POSTGRES_PASSWORD,
                "database": JOBS_POSTGRES_DB,
            },
        }
    },
    "apps": {
        "models": {
            "models": ["validator.models.job", "validator.models.miner_vault", "aerich.models"],
            "default_connection": "default",
        }
    },
}


async def init_db(db_url: Optional[str] = None):
    """
    Initialize Tortoise ORM.

    Args:
        db_url: Optional database URL (postgresql+asyncpg://user:pass@host:port/db)
    """
    if db_url:
        await Tortoise.init(
            db_url=db_url,
            modules={"models": [
                "validator.models.job",
                "validator.models.miner_vault",
                "validator.models.pool_events"
            ]}
        )
    else:
        await Tortoise.init(config=TORTOISE_ORM)

    await Tortoise.generate_schemas(safe=True)


async def close_db():
    """Close Tortoise ORM connections."""
    await Tortoise.close_connections()

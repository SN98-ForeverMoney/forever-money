"""
Tortoise ORM Models for Miner Vault Mining Tracking.

Tracks autonomous vault mining cycles and executor bot interactions.
"""

from decimal import Decimal, getcontext
from tortoise import fields
from tortoise.models import Model

# sqrtPriceX96 and raw token amounts can exceed Python's default 28-digit Decimal precision
getcontext().prec = 78


class VaultMiningCycle(Model):
    """
    Captures on-chain state observed and the rebalance decision made.
    """

    id = fields.IntField(primary_key=True)
    miner_uid = fields.IntField(db_index=True)
    miner_hotkey = fields.CharField(max_length=66, db_index=True)
    vault_address = fields.CharField(max_length=42, db_index=True)
    pool_address = fields.CharField(max_length=42)

    # On-chain state observed
    current_tick = fields.IntField()
    current_price = fields.DecimalField(max_digits=78, decimal_places=0)
    tick_spacing = fields.IntField()
    num_positions = fields.IntField()
    position_tick_lower = fields.IntField(null=True)
    position_tick_upper = fields.IntField(null=True)
    inventory_amount0 = fields.DecimalField(
        max_digits=78, decimal_places=0, default=Decimal("0")
    )
    inventory_amount1 = fields.DecimalField(
        max_digits=78, decimal_places=0, default=Decimal("0")
    )

    # Strategy computation
    volatility = fields.FloatField()
    width_factor = fields.FloatField()
    computed_width = fields.FloatField()

    # Decision
    should_rebalance = fields.BooleanField()
    rebalance_reason = fields.CharField(max_length=32, null=True)
    new_tick_lower = fields.IntField(null=True)
    new_tick_upper = fields.IntField(null=True)
    execution_triggered = fields.BooleanField(default=False)

    created_at = fields.DatetimeField(auto_now_add=True)

    # Relations
    executions: fields.ReverseRelation["VaultMiningExecution"]

    class Meta:
        table = "vault_mining_cycles"
        indexes = (
            ("miner_uid", "vault_address", "created_at"),
            ("vault_address", "should_rebalance"),
        )

    def __str__(self):
        return f"VaultMiningCycle(miner={self.miner_uid}, vault={self.vault_address})"


class VaultMiningExecution(Model):
    """
    Captures the executor bot interaction and result.
    """

    id = fields.IntField(primary_key=True)
    snapshot = fields.ForeignKeyField(
        "models.VaultMiningCycle",
        related_name="executions",
        on_delete=fields.CASCADE,
        null=True,
    )
    miner_uid = fields.IntField(db_index=True)
    miner_hotkey = fields.CharField(max_length=66, db_index=True)
    vault_address = fields.CharField(max_length=42, db_index=True)
    pool_address = fields.CharField(max_length=42)

    # Execution details
    round_id = fields.CharField(max_length=255, unique=True, db_index=True)
    positions_data = fields.JSONField()
    executor_status_code = fields.IntField(null=True)
    tx_hash = fields.CharField(max_length=66, null=True)
    error = fields.TextField(null=True)

    executed_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "vault_mining_executions"
        indexes = (("miner_uid", "vault_address", "executed_at"),)

    def __str__(self):
        return f"VaultMiningExecution(round={self.round_id})"

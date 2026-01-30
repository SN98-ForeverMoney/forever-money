"""
Tortoise ORM Models for Miner-Owned Vaults.

Tracks miner vault registrations, verification status, and balance snapshots.
"""
from decimal import Decimal

from tortoise import fields
from tortoise.models import Model

from validator.utils.env import DEFAULT_MINIMUM_VAULT_BALANCE_USD


class MinerVault(Model):
    """
    Miner-owned vault registration.

    Tracks vault addresses registered by miners for evaluation eligibility.
    """

    id = fields.IntField(primary_key=True)
    miner_uid = fields.IntField(db_index=True)
    miner_hotkey = fields.CharField(max_length=66, db_index=True)
    vault_address = fields.CharField(max_length=42, unique=True, db_index=True)
    chain_id = fields.IntField(default=8453)  # Base L2 default

    # Verification status
    is_verified = fields.BooleanField(default=False, db_index=True)
    is_active = fields.BooleanField(default=True, db_index=True)

    # Balance requirements
    minimum_balance_usd = fields.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal(DEFAULT_MINIMUM_VAULT_BALANCE_USD)
    )

    # Timestamps
    registered_at = fields.DatetimeField(auto_now_add=True)
    verified_at = fields.DatetimeField(null=True)
    last_balance_check = fields.DatetimeField(null=True)
    updated_at = fields.DatetimeField(auto_now=True)

    # Relations
    snapshots: fields.ReverseRelation["VaultSnapshot"]

    class Meta:
        table = "miner_vaults"
        indexes = (
            ("miner_uid", "is_active"),
            ("is_verified", "is_active"),
        )

    def __str__(self):
        return f"MinerVault(miner={self.miner_uid}, vault={self.vault_address})"


class VaultSnapshot(Model):
    """
    Point-in-time snapshot of vault balances.

    Used for tracking vault performance and verifying minimum balance requirements.
    """

    id = fields.IntField(primary_key=True)
    vault = fields.ForeignKeyField(
        "models.MinerVault", related_name="snapshots", on_delete=fields.CASCADE
    )

    # Token balances (raw amounts in wei)
    token0_balance = fields.DecimalField(max_digits=78, decimal_places=0, default=Decimal("0"))
    token1_balance = fields.DecimalField(max_digits=78, decimal_places=0, default=Decimal("0"))

    # USD value at snapshot time
    total_value_usd = fields.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))

    # Blockchain context
    block_number = fields.IntField(db_index=True)

    # Timestamps
    snapshot_at = fields.DatetimeField(auto_now_add=True, db_index=True)

    class Meta:
        table = "vault_snapshots"
        indexes = (
            ("vault_id", "snapshot_at"),
        )

    def __str__(self):
        return f"VaultSnapshot(vault={self.vault_id}, value=${self.total_value_usd})"

"""
Fee snapshot model for tracking vault fee accrual over time.

One snapshot per job per weight-setting cycle (~every tempo).
"""
from tortoise import fields
from tortoise.models import Model


class FeeSnapshot(Model):
    id = fields.IntField(primary_key=True)
    job = fields.ForeignKeyField("models.Job", related_name="fee_snapshots", on_delete=fields.CASCADE)

    # Claimable fees at snapshot time (from static call to claimFees)
    claimable_token0 = fields.DecimalField(max_digits=78, decimal_places=0, default=0)
    claimable_token1 = fields.DecimalField(max_digits=78, decimal_places=0, default=0)

    # Token addresses for USD conversion
    token0_address = fields.CharField(max_length=42, default="")
    token1_address = fields.CharField(max_length=42, default="")

    block_number = fields.IntField(db_index=True)
    chain_id = fields.IntField()
    snapshot_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "fee_snapshots"

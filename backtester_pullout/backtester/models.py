"""Local Tortoise models for the read-only pool event tables.

Mirrors validator/models/pool_events.py field-for-field but lives in this
standalone package so we don't depend on validator imports. Tables themselves
are populated externally by the substreams indexer.

Note the typo `base_poocl_swaps_v2` (missing `l`) on the swap table — that's
the real table name in the indexer.
"""
from tortoise import fields
from tortoise.models import Model


class SwapEvent(Model):
    id = fields.CharField(primary_key=True, max_length=255)
    evt_address = fields.CharField(max_length=42, db_index=True)  # no 0x prefix
    evt_block_number = fields.BigIntField(db_index=True)
    evt_tx_hash = fields.CharField(max_length=66)
    evt_block_time = fields.BigIntField(null=True)

    sqrt_price_x96 = fields.DecimalField(max_digits=78, decimal_places=0)
    tick = fields.IntField()
    amount0 = fields.DecimalField(max_digits=78, decimal_places=0)
    amount1 = fields.DecimalField(max_digits=78, decimal_places=0)
    liquidity = fields.DecimalField(max_digits=78, decimal_places=0)
    sender = fields.CharField(max_length=42)
    recipient = fields.CharField(max_length=42)

    class Meta:
        table = "base_poocl_swaps_v2"  # indexer typo — real table name


class MintEvent(Model):
    id = fields.CharField(primary_key=True, max_length=255)
    evt_address = fields.CharField(max_length=42, db_index=True)
    evt_block_number = fields.BigIntField(db_index=True)
    evt_tx_hash = fields.CharField(max_length=66)
    evt_block_time = fields.BigIntField(null=True)

    tick_lower = fields.IntField()
    tick_upper = fields.IntField()
    amount = fields.DecimalField(max_digits=78, decimal_places=0)
    amount0 = fields.DecimalField(max_digits=78, decimal_places=0)
    amount1 = fields.DecimalField(max_digits=78, decimal_places=0)
    owner = fields.CharField(max_length=42)
    sender = fields.CharField(max_length=42)

    class Meta:
        table = "base_poocl_mints_v2"  # Aerodrome CL mints (note: "poocl" typo, matches indexer)


class BurnEvent(Model):
    id = fields.CharField(primary_key=True, max_length=255)
    evt_address = fields.CharField(max_length=42, db_index=True)
    evt_block_number = fields.BigIntField(db_index=True)
    evt_tx_hash = fields.CharField(max_length=66)
    evt_block_time = fields.BigIntField(null=True)

    tick_lower = fields.IntField()
    tick_upper = fields.IntField()
    amount = fields.DecimalField(max_digits=78, decimal_places=0)
    amount0 = fields.DecimalField(max_digits=78, decimal_places=0)
    amount1 = fields.DecimalField(max_digits=78, decimal_places=0)
    owner = fields.CharField(max_length=42)

    class Meta:
        table = "base_univ3_burns_v2"  # Burns indexed under univ3 namespace (Aerodrome CL = V3 fork, same events)


class CollectEvent(Model):
    id = fields.CharField(primary_key=True, max_length=255)
    evt_address = fields.CharField(max_length=42, db_index=True)
    evt_block_number = fields.BigIntField(db_index=True)
    evt_tx_hash = fields.CharField(max_length=66)
    evt_block_time = fields.BigIntField(null=True)

    tick_lower = fields.IntField()
    tick_upper = fields.IntField()
    amount0 = fields.DecimalField(max_digits=78, decimal_places=0)
    amount1 = fields.DecimalField(max_digits=78, decimal_places=0)
    owner = fields.CharField(max_length=42)
    recipient = fields.CharField(max_length=42)

    class Meta:
        table = "base_univ3_collects_v2"  # Collects indexed under univ3 namespace

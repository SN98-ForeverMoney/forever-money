"""
Tortoise ORM Models for Pool Events (Swaps, Mints, Burns, Collects).

These models represent read-only tables populated by a subgraph indexer
that tracks on-chain events for Aerodrome/Uniswap V3 pools.
"""
from datetime import datetime
from typing import Optional

from tortoise import fields
from tortoise.models import Model


class SwapEvent(Model):
    """
    Swap events from Uniswap V3 / Aerodrome pools.

    Tracks token exchanges with price and liquidity information.
    """

    id = fields.IntField(primary_key=True)
    evt_address = fields.CharField(
        max_length=42, db_index=True
    )  # Pool address (without 0x)
    evt_block_number = fields.BigIntField(db_index=True)
    evt_tx_hash = fields.CharField(max_length=66)
    evt_block_time = fields.BigIntField(null=True)  # Unix timestamp

    # Swap details
    sqrt_price_x96 = fields.DecimalField(max_digits=78, decimal_places=0)  # uint160
    tick = fields.IntField()
    amount0 = fields.DecimalField(max_digits=78, decimal_places=0)  # int256
    amount1 = fields.DecimalField(max_digits=78, decimal_places=0)  # int256
    liquidity = fields.DecimalField(max_digits=78, decimal_places=0)  # uint128
    sender = fields.CharField(max_length=42)
    recipient = fields.CharField(max_length=42)

    class Meta:
        table = "swaps"
        indexes = (("evt_address", "evt_block_number"),)

    def __str__(self):
        return f"Swap(pool={self.evt_address}, block={self.evt_block_number})"


class MintEvent(Model):
    """
    Mint events (liquidity additions) from Uniswap V3 / Aerodrome pools.

    Tracks when liquidity providers add liquidity to specific tick ranges.
    """

    id = fields.IntField(primary_key=True)
    evt_address = fields.CharField(
        max_length=42, db_index=True
    )  # Pool address (without 0x)
    evt_block_number = fields.BigIntField(db_index=True)
    evt_tx_hash = fields.CharField(max_length=66)
    evt_block_time = fields.BigIntField(null=True)  # Unix timestamp

    # Mint details
    tick_lower = fields.IntField()
    tick_upper = fields.IntField()
    amount = fields.DecimalField(max_digits=78, decimal_places=0)  # Liquidity amount
    amount0 = fields.DecimalField(max_digits=78, decimal_places=0)  # Token0 amount
    amount1 = fields.DecimalField(max_digits=78, decimal_places=0)  # Token1 amount
    owner = fields.CharField(max_length=42)
    sender = fields.CharField(max_length=42)

    class Meta:
        table = "mints"
        indexes = (("evt_address", "evt_block_number"),)

    def __str__(self):
        return f"Mint(pool={self.evt_address}, block={self.evt_block_number})"


class BurnEvent(Model):
    """
    Burn events (liquidity removals) from Uniswap V3 / Aerodrome pools.

    Tracks when liquidity providers remove liquidity from specific tick ranges.
    """

    id = fields.IntField(primary_key=True)
    evt_address = fields.CharField(
        max_length=42, db_index=True
    )  # Pool address (without 0x)
    evt_block_number = fields.BigIntField(db_index=True)
    evt_tx_hash = fields.CharField(max_length=66)
    evt_block_time = fields.BigIntField(null=True)  # Unix timestamp

    # Burn details
    tick_lower = fields.IntField()
    tick_upper = fields.IntField()
    amount = fields.DecimalField(max_digits=78, decimal_places=0)  # Liquidity amount
    amount0 = fields.DecimalField(max_digits=78, decimal_places=0)  # Token0 amount
    amount1 = fields.DecimalField(max_digits=78, decimal_places=0)  # Token1 amount
    owner = fields.CharField(max_length=42)

    class Meta:
        table = "burns"
        indexes = (("evt_address", "evt_block_number"),)

    def __str__(self):
        return f"Burn(pool={self.evt_address}, block={self.evt_block_number})"


class CollectEvent(Model):
    """
    Collect events (fee collections) from Uniswap V3 / Aerodrome pools.

    Tracks when liquidity providers collect their earned fees.
    """

    id = fields.IntField(primary_key=True)
    evt_address = fields.CharField(
        max_length=42, db_index=True
    )  # Pool address (without 0x)
    evt_block_number = fields.BigIntField(db_index=True)
    evt_tx_hash = fields.CharField(max_length=66)
    evt_block_time = fields.BigIntField(null=True)  # Unix timestamp

    # Collect details
    tick_lower = fields.IntField()
    tick_upper = fields.IntField()
    amount0 = fields.DecimalField(
        max_digits=78, decimal_places=0
    )  # Fee amount in token0
    amount1 = fields.DecimalField(
        max_digits=78, decimal_places=0
    )  # Fee amount in token1
    owner = fields.CharField(max_length=42)
    recipient = fields.CharField(max_length=42)

    class Meta:
        table = "collects"
        indexes = (
            ("evt_address", "evt_block_number"),
            ("owner", "evt_block_number"),
        )

    def __str__(self):
        return f"Collect(pool={self.evt_address}, block={self.evt_block_number})"

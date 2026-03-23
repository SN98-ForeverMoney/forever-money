"""
Miner tracking repository for persisting vault mining activity to DB.

Wraps all DB writes in try/except so failures never break the mining loop.
"""

import logging
from decimal import Decimal
from typing import Optional, List

from validator.models.miner_tracking import VaultMiningCycle, VaultMiningExecution

logger = logging.getLogger(__name__)


class MiningRepository:
    """Handles all DB persistence for the miner's vault mining activity."""

    def __init__(self, miner_uid: Optional[int], miner_hotkey: str, width_factor: float):
        self.miner_uid = miner_uid
        self.miner_hotkey = miner_hotkey
        self.width_factor = width_factor

    async def save_mining_snapshot(
        self,
        *,
        vault_address: str,
        pool_address: str,
        current_tick: int,
        current_price: int,
        tick_spacing: int,
        current_positions: List,
        inventory,
        volatility: float,
        computed_width: float,
        rebalance_reason: Optional[str],
        new_position=None,
        execution_triggered: bool,
    ) -> Optional[VaultMiningCycle]:
        """Record a vault mining snapshot. Returns the snapshot or None on failure."""
        try:
            return await VaultMiningCycle.create(
                miner_uid=self.miner_uid,
                miner_hotkey=self.miner_hotkey,
                vault_address=vault_address,
                pool_address=pool_address,
                current_tick=current_tick,
                current_price=Decimal(str(current_price)),
                tick_spacing=tick_spacing,
                num_positions=len(current_positions),
                position_tick_lower=current_positions[0].tick_lower if current_positions else None,
                position_tick_upper=current_positions[0].tick_upper if current_positions else None,
                inventory_amount0=Decimal(str(inventory.amount0)),
                inventory_amount1=Decimal(str(inventory.amount1)),
                volatility=volatility,
                width_factor=self.width_factor,
                computed_width=computed_width,
                should_rebalance=rebalance_reason is not None,
                rebalance_reason=rebalance_reason,
                new_tick_lower=new_position.tick_lower if new_position else None,
                new_tick_upper=new_position.tick_upper if new_position else None,
                execution_triggered=execution_triggered,
            )
        except Exception as e:
            logger.warning(f"Failed to save vault mining snapshot to DB: {e}")
            return None

    async def create_mining_execution(
        self,
        *,
        snapshot: Optional[VaultMiningCycle],
        vault_address: str,
        pool_address: str,
        round_id: str,
        positions_data: List[dict],
        executor_status_code: Optional[int],
        tx_hash: Optional[str],
        error: Optional[str],
    ) -> None:
        """Record a vault mining execution."""
        try:
            await VaultMiningExecution.create(
                snapshot=snapshot,
                miner_uid=self.miner_uid,
                miner_hotkey=self.miner_hotkey,
                vault_address=vault_address,
                pool_address=pool_address,
                round_id=round_id,
                positions_data=positions_data,
                executor_status_code=executor_status_code,
                tx_hash=tx_hash,
                error=error,
            )
        except Exception as e:
            logger.warning(f"Failed to save vault mining execution to DB: {e}")

"""
Query miners for rebalance decisions via RebalanceQuery synapse.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from protocol.models import Inventory, Position
from protocol.synapses import RebalanceQuery

logger = logging.getLogger(__name__)


async def query_miner_for_rebalance(
    dendrite,
    metagraph,
    *,
    miner_uid: int,
    job_id: str,
    sn_liquidity_manager_address: str,
    pair_address: str,
    round_id: str,
    round_type: str,
    block_number: int,
    current_price,
    current_positions: List[Position],
    inventory: Inventory,
    rebalances_so_far: int,
    tick_spacing: int = 200,
    timeout: int = 5,
) -> Optional[RebalanceQuery]:
    """
    Query a single miner for a rebalance decision.

    Returns:
        RebalanceQuery response, or None on timeout/error.
    """
    synapse = RebalanceQuery(
        job_id=job_id,
        sn_liquidity_manager_address=sn_liquidity_manager_address,
        pair_address=pair_address,
        round_id=round_id,
        round_type=round_type,
        block_number=block_number,
        current_price=current_price,
        current_positions=current_positions,
        inventory_remaining={"amount0": inventory.amount0, "amount1": inventory.amount1},
        rebalances_so_far=rebalances_so_far,
        tick_spacing=tick_spacing,
    )
    try:
        responses = await dendrite(
            axons=[metagraph.axons[miner_uid]],
            synapse=synapse,
            timeout=timeout,
            deserialize=True,
        )
        response = responses[0] if responses else None
        if response and hasattr(response, "accepted"):
            return response
        refusal = getattr(response, "refusal_reason", None) if response else "No response"
        logger.debug(f"Miner refused or failed. Refusal reason: {refusal}")
        return None
    except Exception as e:
        logger.error(f"Error querying miner {miner_uid}: {e}")
        return None

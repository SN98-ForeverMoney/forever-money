"""
Query miners for rebalance decisions via RebalanceQuery synapse.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from protocol.models import Inventory, Position
from protocol.synapses import RebalanceQuery

logger = logging.getLogger(__name__)


def _parse_single_response(response, miner_uid: int) -> Optional[RebalanceQuery]:
    """Parse one dendrite response into RebalanceQuery or None."""
    if response and hasattr(response, "accepted"):
        return response
    refusal = (
        getattr(response, "refusal_reason", None) if response else "No response"
    )
    logger.debug(f"Miner {miner_uid} refused or failed. Refusal reason: {refusal}")
    return None


async def query_miners_for_rebalance(
    dendrite,
    metagraph,
    *,
    miner_uids: List[int],
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
) -> List[Optional[RebalanceQuery]]:
    """
    Query multiple miners in one dendrite call (same synapse, multiple axons).

    Returns:
        List of RebalanceQuery or None, in the same order as miner_uids.
    """
    if not miner_uids:
        return []

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
        axons = [metagraph.axons[uid] for uid in miner_uids]
        responses = await dendrite(
            axons=axons,
            synapse=synapse,
            timeout=timeout,
            deserialize=True,
        )
        # responses order matches axons order (miner_uids)
        return [
            _parse_single_response(
                responses[i] if i < len(responses) else None, miner_uids[i]
            )
            for i in range(len(miner_uids))
        ]
    except Exception as e:
        logger.error(f"Error batch-querying miners {miner_uids}: {e}")
        return [None for _ in miner_uids]


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
    responses = await query_miners_for_rebalance(
        dendrite,
        metagraph,
        miner_uids=[miner_uid],
        job_id=job_id,
        sn_liquidity_manager_address=sn_liquidity_manager_address,
        pair_address=pair_address,
        round_id=round_id,
        round_type=round_type,
        block_number=block_number,
        current_price=current_price,
        current_positions=current_positions,
        inventory=inventory,
        rebalances_so_far=rebalances_so_far,
        tick_spacing=tick_spacing,
        timeout=timeout,
    )
    return responses[0] if responses else None

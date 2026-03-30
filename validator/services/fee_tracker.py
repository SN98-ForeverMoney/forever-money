"""
Fee tracker service for reading vault fee accrual directly from chain.

Uses static calls to AeroCLPositionManager.claimFees() to read
claimable (accrued) fees without actually claiming them.

Snapshots are taken once per weight-setting cycle (~every tempo).
Revenue for a tempo = delta between current and previous snapshot.
"""
import logging
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from web3 import Web3

from validator.models.job import Job
from validator.models.fee_snapshot import FeeSnapshot
from validator.services.price import PriceService
from validator.utils.web3 import AsyncWeb3Helper, ZERO_ADDRESS

logger = logging.getLogger(__name__)


async def get_claimable_fees(
    chain_id: int,
    liquidity_manager_address: str,
    pool_address: str,
) -> Tuple[Dict[str, int], str, str]:
    """
    Read claimable fees for a vault via static call to claimFees().

    Returns:
        (fees_dict, token0_address, token1_address)
        fees_dict maps token_address -> claimable_amount in wei
    """
    w3 = AsyncWeb3Helper.make_web3(chain_id)
    lm = w3.make_contract_by_name("LiquidityManager", liquidity_manager_address)
    pool = w3.make_contract_by_name("ICLPool", pool_address)

    token0 = await pool.functions.token0().call()
    token1 = await pool.functions.token1().call()

    # Find position manager
    pm_addr = None
    for token in [token0, token1]:
        try:
            addr = await lm.functions.akAddressToPositionManager(
                Web3.to_checksum_address(token)
            ).call()
            if addr != ZERO_ADDRESS:
                pm_addr = addr
                break
        except Exception:
            continue

    if pm_addr is None:
        return {token0: 0, token1: 0}, token0, token1

    pm = w3.make_contract_by_name("AeroCLPositionManager", pm_addr)

    try:
        result = await pm.functions.claimFees().call(
            {"from": Web3.to_checksum_address(liquidity_manager_address)}
        )
    except Exception as e:
        logger.error(f"claimFees() static call failed for {liquidity_manager_address}: {e}")
        return {token0: 0, token1: 0}, token0, token1

    fees: Dict[str, int] = {token0: 0, token1: 0}
    for token_addr, amount, _ in result:
        if token_addr.lower() == token0.lower():
            fees[token0] += amount
        elif token_addr.lower() == token1.lower():
            fees[token1] += amount

    return fees, token0, token1


async def take_snapshots_for_all_jobs(jobs: List[Job]) -> List[FeeSnapshot]:
    """
    Take fee snapshots for all active jobs. Called once per weight-setting cycle.
    """
    snapshots = []
    for job in jobs:
        try:
            w3 = AsyncWeb3Helper.make_web3(job.chain_id)
            current_block = await w3.web3.eth.block_number

            fees, token0, token1 = await get_claimable_fees(
                job.chain_id,
                job.sn_liquidity_manager_address,
                job.pair_address,
            )

            snapshot = await FeeSnapshot.create(
                job=job,
                claimable_token0=Decimal(str(fees.get(token0, 0))),
                claimable_token1=Decimal(str(fees.get(token1, 0))),
                token0_address=token0,
                token1_address=token1,
                block_number=current_block,
                chain_id=job.chain_id,
            )
            snapshots.append(snapshot)

            logger.info(
                f"[FEE_SNAPSHOT] job={job.job_id} block={current_block} "
                f"token0_fees={fees.get(token0, 0)} token1_fees={fees.get(token1, 0)}"
            )
        except Exception as e:
            logger.error(f"Failed to take fee snapshot for job {job.job_id}: {e}")

    return snapshots


async def get_tempo_revenue_usd(jobs: List[Job]) -> float:
    """
    Get total revenue across all jobs for the last tempo.

    Takes a fresh snapshot, then diffs against the previous one per job.
    Converts the delta to USD.

    Called during weight-setting cycle.
    """
    # Take fresh snapshots
    current_snapshots = await take_snapshots_for_all_jobs(jobs)

    total_usd = 0.0
    for snapshot in current_snapshots:
        # Find previous snapshot for this job
        previous = await FeeSnapshot.filter(
            job_id=snapshot.job_id,
            id__lt=snapshot.id,
        ).order_by("-id").first()

        if previous is None:
            # First snapshot for this job — no delta to compute
            logger.info(f"[FEE_REVENUE] job={snapshot.job_id}: first snapshot, no revenue yet")
            continue

        delta0 = max(0, int(snapshot.claimable_token0) - int(previous.claimable_token0))
        delta1 = max(0, int(snapshot.claimable_token1) - int(previous.claimable_token1))

        if delta0 == 0 and delta1 == 0:
            continue

        try:
            p0 = await PriceService.get_token_price(
                snapshot.token0_address, snapshot.chain_id
            )
            p1 = await PriceService.get_token_price(
                snapshot.token1_address, snapshot.chain_id
            )
            # TODO: use actual token decimals instead of assuming 18
            revenue_usd = (float(delta0) / 1e18) * p0 + (float(delta1) / 1e18) * p1
            total_usd += revenue_usd

            logger.info(
                f"[FEE_REVENUE] job={snapshot.job_id} "
                f"delta0={delta0} delta1={delta1} "
                f"p0={p0:.4f} p1={p1:.4f} revenue_usd=${revenue_usd:.4f}"
            )
        except Exception as e:
            logger.warning(f"Failed to price fees for job {snapshot.job_id}: {e}")

    logger.info(f"[FEE_REVENUE] Total tempo revenue: ${total_usd:.4f}")
    return total_usd

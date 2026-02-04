"""
Evaluation and live round loops.

Block-simulation loops that query miners for rebalance decisions,
simulate (eval) or execute on-chain (live), and score performance.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List

from protocol.models import Inventory, Position
from validator.models.job import Job, Round
from validator.services.scorer import Scorer
from validator.utils.math import UniswapV3Math

from validator.orchestrator.executor import execute_strategy_onchain
from validator.orchestrator.miner_query import query_miner_for_rebalance

logger = logging.getLogger(__name__)

# Skip rebalance if desired positions differ from current by less than this.
# Reduces unnecessary rebalances from latency/price drift, especially for in-range positions.
REBALANCE_TOLERANCE = 0.02  # 2%


def _within_tolerance(a: int, b: int, tolerance: float) -> bool:
    """Return True if a and b are within relative tolerance."""
    if a == b:
        return True
    denom = max(abs(a), abs(b), 1)
    return abs(a - b) / denom <= tolerance


def _positions_within_tolerance(
    current: List[Position],
    desired: List[Position],
    tolerance: float = REBALANCE_TOLERANCE,
) -> bool:
    """
    Return True if desired positions are within tolerance of current; no rebalance needed.
    Compares tick ranges and allocations; skips small allocation drift from latency/price.
    """
    if len(current) != len(desired):
        return False
    current_by_range = {(p.tick_lower, p.tick_upper): p for p in current}
    for d in desired:
        key = (d.tick_lower, d.tick_upper)
        if key not in current_by_range:
            return False
        c = current_by_range[key]
        a0_c, a1_c = int(c.allocation0), int(c.allocation1)
        a0_d, a1_d = int(d.allocation0), int(d.allocation1)
        if not _within_tolerance(a0_c, a0_d, tolerance) or not _within_tolerance(
            a1_c, a1_d, tolerance
        ):
            return False
    return True


def _round_type_str(round_: Round) -> str:
    rt = getattr(round_, "round_type", None)
    if rt is None:
        return "evaluation"
    return getattr(rt, "value", str(rt))


def _serialize_history_item(item: Dict) -> Dict:
    out = item.copy()
    inv = out.get("inventory")
    if inv is not None and hasattr(inv, "dict"):
        out["inventory"] = inv.dict()
    for key in ("new_positions", "old_positions"):
        arr = out.get(key)
        if arr is None:
            continue
        out[key] = [p.dict() for p in arr if hasattr(p, "dict")]
    return out


def _serialize_metrics(metrics: Dict[str, Any]) -> Dict[str, Any]:
    out = metrics.copy()
    for key in ("initial_inventory", "final_inventory"):
        v = out.get(key)
        if v is not None and hasattr(v, "dict"):
            out[key] = v.dict()
    return out


async def run_with_miner_for_evaluation(
    *,
    miner_uid: int,
    job: Job,
    round_: Round,
    initial_positions: List[Position],
    start_block: int,
    initial_inventory: Inventory,
    rebalance_check_interval: int,
    liq_manager,
    job_repository,
    dendrite,
    metagraph,
    backtester,
    get_block_fn: Callable[[int], Any],
) -> Dict[str, Any]:
    """
    Run backtest, querying miner for rebalancing decisions.

    Returns:
        Dict with accepted, refusal_reason, rebalance_history, final_positions,
        performance_metrics, score (if accepted), total_query_time_ms.
    """
    logger.info(f"[ROUND={round_.round_id}] Running backtest for miner {miner_uid}")

    current_positions = list(initial_positions)
    current_inventory = initial_inventory
    rebalance_history: List[Dict] = [
        {"block": start_block - 1, "new_positions": initial_positions, "inventory": initial_inventory}
    ]
    total_query_time_ms = 0
    rebalances_so_far = 0
    current_block = start_block
    rtype = _round_type_str(round_)
    tick_spacing = await liq_manager.get_tick_spacing()

    while round_.round_deadline >= datetime.now(timezone.utc):
        if (current_block - start_block) % rebalance_check_interval == 0:
            logger.debug(f"Querying miner {miner_uid} at block {current_block}")
            price_at_query = await liq_manager.get_current_price()
            t0 = time.time()
            response = await query_miner_for_rebalance(
                dendrite,
                metagraph,
                miner_uid=miner_uid,
                job_id=job.job_id,
                sn_liquidity_manager_address=job.sn_liquidity_manager_address,
                pair_address=job.pair_address,
                round_id=round_.round_id,
                round_type=rtype,
                block_number=current_block,
                current_price=price_at_query,
                current_positions=current_positions,
                inventory=current_inventory,
                rebalances_so_far=rebalances_so_far,
                tick_spacing=tick_spacing,
            )
            total_query_time_ms += int((time.time() - t0) * 1000)

            if response is None:
                logger.warning(f"Miner {miner_uid} timeout/error at block {current_block}")
                return {
                    "accepted": False,
                    "refusal_reason": "Timeout or error",
                    "rebalance_history": rebalance_history,
                    "final_positions": current_positions,
                    "performance_metrics": {},
                    "total_query_time_ms": total_query_time_ms,
                }
            if not response.accepted:
                logger.info(f"Miner {miner_uid} refused job: {response.refusal_reason}")
                return {
                    "accepted": False,
                    "refusal_reason": response.refusal_reason,
                    "rebalance_history": rebalance_history,
                    "final_positions": current_positions,
                    "performance_metrics": {},
                    "total_query_time_ms": total_query_time_ms,
                }
            if response.desired_positions is not None:
                if _positions_within_tolerance(
                    current_positions, response.desired_positions
                ):
                    logger.debug(
                        f"Positions within {REBALANCE_TOLERANCE*100:.0f}% tolerance, "
                        f"skipping rebalance at block {current_block}"
                    )
                else:
                    logger.debug(
                        f"Miner {miner_uid} rebalancing at block {current_block}: "
                        f"{len(response.desired_positions)} positions"
                    )
                    rebalance_price = await liq_manager.get_current_price()
                    total_a0, total_a1 = 0, 0
                    for pos in response.desired_positions:
                        _, a0, a1 = UniswapV3Math.position_liquidity_and_used_amounts(
                            pos.tick_lower,
                            pos.tick_upper,
                            int(pos.allocation0),
                            int(pos.allocation1),
                            rebalance_price,
                        )
                        total_a0 += a0
                        total_a1 += a1
                    amount_0_int = int(initial_inventory.amount0) - total_a0
                    amount_1_int = int(initial_inventory.amount1) - total_a1
                    if amount_0_int < 0 or amount_1_int < 0:
                        return {
                            "accepted": False,
                            "refusal_reason": None,
                            "rebalance_history": rebalance_history,
                            "final_positions": current_positions,
                            "performance_metrics": {},
                            "total_query_time_ms": total_query_time_ms,
                        }
                    current_inventory = Inventory(
                        amount0=str(amount_0_int), amount1=str(amount_1_int)
                    )
                    rebalance_history.append({
                        "block": current_block,
                        "price": rebalance_price,
                        "price_in_query": price_at_query,
                        "old_positions": current_positions,
                        "new_positions": response.desired_positions,
                        "inventory": current_inventory,
                    })
                    current_positions = response.desired_positions
                    rebalances_so_far += 1
        else:
            await asyncio.sleep(1)
        current_block = await get_block_fn(job.chain_id)

    performance_metrics = await backtester.evaluate_positions_performance(
        job.pair_address,
        rebalance_history,
        start_block,
        current_block,
        initial_inventory,
        job.fee_rate,
    )
    logger.info(
        f"Backtest complete for miner {miner_uid}: {len(rebalance_history)} rebalances, "
        f"PnL: {performance_metrics.get('pnl', 0):.4f}"
    )
    miner_score_val = await Scorer.score_pol_strategy(metrics=performance_metrics)
    # Score/participation updates happen in run_evaluation_round after winner selection
    # so tie-breaking uses pre-update combined_score.

    serialized_history = [_serialize_history_item(h) for h in rebalance_history]
    serialized_metrics = _serialize_metrics(performance_metrics)
    final_positions_ser = [p.dict() for p in current_positions if hasattr(p, "dict")]
    return {
        "accepted": True,
        "refusal_reason": None,
        "rebalance_history": serialized_history,
        "final_positions": final_positions_ser,
        "performance_metrics": serialized_metrics,
        "score": miner_score_val,
        "total_query_time_ms": total_query_time_ms,
    }


async def run_with_miner_for_live(
    *,
    miner_uid: int,
    job: Job,
    round_: Round,
    initial_positions: List[Position],
    start_block: int,
    initial_inventory: Inventory,
    rebalance_check_interval: int,
    liq_manager,
    job_repository,
    config: Dict[str, Any],
    dendrite,
    metagraph,
    backtester,
    get_block_fn: Callable[[int], Any],
) -> Dict[str, Any]:
    """
    Run live round loop, executing miner decisions on-chain.

    Returns:
        Dict with accepted, score, rebalance_history, total_query_time_ms,
        execution_failures, execution_results.
    """
    current_positions = list(initial_positions)
    current_inventory = initial_inventory
    rebalance_history: List[Dict] = [
        {"block": start_block - 1, "new_positions": initial_positions, "inventory": initial_inventory}
    ]
    total_query_time_ms = 0
    rebalances_so_far = 0
    execution_failures = 0
    execution_results: List[Dict] = []
    current_block = start_block
    rtype = _round_type_str(round_)
    tick_spacing = await liq_manager.get_tick_spacing()

    while round_.round_deadline >= datetime.now(timezone.utc):
        if (current_block - start_block) % rebalance_check_interval == 0:
            price_at_query = await liq_manager.get_current_price()
            t0 = time.time()
            response = await query_miner_for_rebalance(
                dendrite,
                metagraph,
                miner_uid=miner_uid,
                job_id=job.job_id,
                sn_liquidity_manager_address=job.sn_liquidity_manager_address,
                pair_address=job.pair_address,
                round_id=round_.round_id,
                round_type=rtype,
                block_number=current_block,
                current_price=price_at_query,
                current_positions=current_positions,
                inventory=current_inventory,
                rebalances_so_far=rebalances_so_far,
                tick_spacing=tick_spacing,
            )
            total_query_time_ms += int((time.time() - t0) * 1000)

            if response and response.accepted and response.desired_positions is not None:
                if _positions_within_tolerance(
                    current_positions, response.desired_positions
                ):
                    logger.debug(
                        f"Positions within {REBALANCE_TOLERANCE*100:.0f}% tolerance, "
                        f"skipping on-chain execution at block {current_block}"
                    )
                else:
                    exec_result = await execute_strategy_onchain(
                        job_repository,
                        config,
                        job,
                        round_,
                        miner_uid,
                        rebalance_history + [
                            {"new_positions": response.desired_positions}
                        ],
                        timeout=30,
                    )
                    execution_results.append({
                        "block": current_block,
                        "success": exec_result["success"],
                        "execution_id": exec_result.get("execution_id"),
                        "tx_hash": exec_result.get("tx_hash"),
                        "error": exec_result.get("error"),
                    })
                    if exec_result["success"]:
                        rebalance_price = await liq_manager.get_current_price()
                        total_a0, total_a1 = 0, 0
                        for pos in response.desired_positions:
                            _, a0, a1 = UniswapV3Math.position_liquidity_and_used_amounts(
                                pos.tick_lower, pos.tick_upper,
                                int(pos.allocation0), int(pos.allocation1),
                                rebalance_price,
                            )
                            total_a0 += a0
                            total_a1 += a1
                        amount_0_int = max(0, int(initial_inventory.amount0) - total_a0)
                        amount_1_int = max(0, int(initial_inventory.amount1) - total_a1)
                        current_inventory = Inventory(
                            amount0=str(amount_0_int), amount1=str(amount_1_int)
                        )
                        rebalance_history.append({
                            "block": current_block,
                            "price": rebalance_price,
                            "price_in_query": price_at_query,
                            "old_positions": current_positions,
                            "new_positions": response.desired_positions,
                            "inventory": current_inventory,
                            "execution_id": exec_result.get("execution_id"),
                            "tx_hash": exec_result.get("tx_hash"),
                        })
                        current_positions = response.desired_positions
                        rebalances_so_far += 1
                    else:
                        execution_failures += 1
                        logger.error(
                            f"Failed to execute strategy on-chain for miner {miner_uid} "
                            f"at block {current_block}: {exec_result.get('error')}"
                        )
        else:
            await asyncio.sleep(1)
        current_block = await get_block_fn(job.chain_id)

    performance_metrics = await backtester.evaluate_positions_performance(
        job.pair_address,
        rebalance_history,
        start_block,
        current_block,
        initial_inventory,
        job.fee_rate,
    )
    score = await Scorer.score_pol_strategy(metrics=performance_metrics)
    return {
        "accepted": True,
        "score": score,
        "rebalance_history": rebalance_history,
        "total_query_time_ms": total_query_time_ms,
        "execution_failures": execution_failures,
        "execution_results": execution_results,
    }

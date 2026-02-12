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
    rebalance_history: List[Dict] = []
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
            if response.desired_positions is None:
                logger.info(
                    f"Miner {miner_uid} returned desired_positions=None (likely not "
                    "running / no response), score=0"
                )
                return {
                    "accepted": False,
                    "refusal_reason": "No desired positions (miner not running)",
                    "rebalance_history": rebalance_history,
                    "final_positions": current_positions,
                    "performance_metrics": {},
                    "score": 0.0,
                    "total_query_time_ms": total_query_time_ms,
                }
            elif _positions_within_tolerance(
                current_positions, response.desired_positions
            ):
                logger.info(
                    f"Positions within {REBALANCE_TOLERANCE*100:.0f}% tolerance, "
                    f"skipping rebalance at block {current_block}"
                )
            else:
                logger.info(
                    f"Miner {miner_uid} rebalancing at block {current_block}: "
                    f"{len(response.desired_positions)} positions"
                )
                rebalance_price = await liq_manager.get_current_price()
                total_a0, total_a1 = 0, 0
                for pos in response.desired_positions:
                    _, a0, a1 = UniswapV3Math.position_liquidity_and_used_amounts(
                        pos.tick_lower,
                        pos.tick_upper,
                        rebalance_price,
                        int(pos.allocation0),
                        int(pos.allocation1),
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
    iv = performance_metrics.get("initial_value")
    fv = performance_metrics.get("final_value")
    return_pct = (
        (float(fv) - float(iv)) / float(iv) * 100
        if iv is not None and fv is not None and float(iv) > 0
        else None
    )
    return_str = f", return: {return_pct:.2f}%" if return_pct is not None else ""
    logger.info(
        f"Backtest complete for miner {miner_uid}: {len(rebalance_history)} rebalances"
        f", initial_value={iv}, final_value={fv}{return_str}"
    )
    miner_score_val = await Scorer.score_pol_strategy(metrics=performance_metrics)
    # Score/participation updates happen in run_evaluation_round after winner selection
    # so tie-breaking uses pre-update combined_score.
    
    logger.info(f"Miner {miner_uid} score: {miner_score_val}")

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


async def run_with_miners_batch_for_evaluation(
    *,
    miner_uids: List[int],
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
) -> Dict[int, Dict[str, Any]]:
    """
    Run backtest for multiple miners with correct per-miner state.

    At each rebalance step we query all miners in parallel (one dendrite call per
    miner, each with that miner's own current_positions, current_inventory,
    rebalances_so_far). So each miner receives the correct synapse (their own
    state). Wall-clock time per step is ~one RTT because calls are concurrent.

    Returns:
        Dict mapping miner_uid -> result dict (accepted, score, rebalance_history, etc.)
    """
    if not miner_uids:
        return {}

    logger.info(
        f"[ROUND={round_.round_id}] Starting parallel per-miner evaluation: "
        f"job={job.job_id}, miners={len(miner_uids)} uids={miner_uids[:10]}{'...' if len(miner_uids) > 10 else ''}, "
        f"start_block={start_block}, rebalance_interval={rebalance_check_interval}"
    )
    rtype = _round_type_str(round_)
    tick_spacing = await liq_manager.get_tick_spacing()

    # Per-miner state (each miner gets their own evolving state)
    per_miner_positions: Dict[int, List[Position]] = {
        uid: list(initial_positions) for uid in miner_uids
    }
    per_miner_inventory: Dict[int, Inventory] = {
        uid: initial_inventory for uid in miner_uids
    }
    per_miner_rebalances_so_far: Dict[int, int] = {uid: 0 for uid in miner_uids}
    per_miner_history: Dict[int, List[Dict]] = {uid: [] for uid in miner_uids}
    per_miner_refused: Dict[int, Any] = {}  # uid -> refusal_reason or None
    per_miner_query_time_ms: Dict[int, int] = {uid: 0 for uid in miner_uids}

    current_block = start_block
    step_count = 0

    while round_.round_deadline >= datetime.now(timezone.utc):
        if (current_block - start_block) % rebalance_check_interval == 0:
            step_count += 1
            active_count = len(miner_uids) - len(per_miner_refused)
            logger.debug(
                f"[ROUND={round_.round_id}] Rebalance step {step_count} at block {current_block}: "
                f"{active_count} miners still active (refused so far: {len(per_miner_refused)})"
            )
            price_at_query = await liq_manager.get_current_price()

            # One query per miner, each with that miner's own state; run all in parallel
            async def query_one(uid: int):
                if uid in per_miner_refused:
                    return uid, None
                return uid, await query_miner_for_rebalance(
                    dendrite,
                    metagraph,
                    miner_uid=uid,
                    job_id=job.job_id,
                    sn_liquidity_manager_address=job.sn_liquidity_manager_address,
                    pair_address=job.pair_address,
                    round_id=round_.round_id,
                    round_type=rtype,
                    block_number=current_block,
                    current_price=price_at_query,
                    current_positions=per_miner_positions[uid],
                    inventory=per_miner_inventory[uid],
                    rebalances_so_far=per_miner_rebalances_so_far[uid],
                    tick_spacing=tick_spacing,
                )

            t0 = time.time()
            query_results = await asyncio.gather(
                *[query_one(uid) for uid in miner_uids]
            )
            elapsed_ms = int((time.time() - t0) * 1000)
            for uid in miner_uids:
                per_miner_query_time_ms[uid] = per_miner_query_time_ms.get(uid, 0) + elapsed_ms
            logger.debug(
                f"[ROUND={round_.round_id}] Step {step_count} parallel query done: "
                f"block={current_block}, elapsed_ms={elapsed_ms}"
            )

            rebalance_price = await liq_manager.get_current_price()

            for uid, response in query_results:
                if uid in per_miner_refused:
                    continue
                if response is None or not response.accepted:
                    reason = (
                        getattr(response, "refusal_reason", None)
                        if response
                        else "Timeout or error"
                    )
                    per_miner_refused[uid] = reason
                    logger.info(f"Miner {uid} refused or failed: {reason}")
                    continue
                if response.desired_positions is None:
                    per_miner_refused[uid] = "Miner not running"
                    continue
                if _positions_within_tolerance(
                    per_miner_positions[uid], response.desired_positions
                ):
                    continue
                inv = per_miner_inventory[uid]
                total_a0, total_a1 = 0, 0
                for pos in response.desired_positions:
                    _, a0, a1 = UniswapV3Math.position_liquidity_and_used_amounts(
                        pos.tick_lower,
                        pos.tick_upper,
                        rebalance_price,
                        int(pos.allocation0),
                        int(pos.allocation1),
                    )
                    total_a0 += a0
                    total_a1 += a1
                inv_0 = int(inv.amount0) - total_a0
                inv_1 = int(inv.amount1) - total_a1
                if inv_0 < 0 or inv_1 < 0:
                    per_miner_refused[uid] = "Insufficient inventory from desired positions"
                    continue
                new_inventory = Inventory(amount0=str(inv_0), amount1=str(inv_1))
                entry = {
                    "block": current_block,
                    "price": rebalance_price,
                    "price_in_query": price_at_query,
                    "old_positions": per_miner_positions[uid],
                    "new_positions": response.desired_positions,
                    "inventory": new_inventory,
                }
                per_miner_history[uid].append(entry)
                per_miner_positions[uid] = response.desired_positions
                per_miner_inventory[uid] = new_inventory
                per_miner_rebalances_so_far[uid] = per_miner_rebalances_so_far[uid] + 1
                logger.debug(
                    f"[ROUND={round_.round_id}] Miner {uid} rebalanced at block {current_block}: "
                    f"rebalances_so_far={per_miner_rebalances_so_far[uid]}"
                )

        else:
            await asyncio.sleep(1)
        current_block = await get_block_fn(job.chain_id)

    refused_count = len(per_miner_refused)
    completed_count = sum(1 for uid in miner_uids if uid not in per_miner_refused and per_miner_history[uid])
    logger.info(
        f"[ROUND={round_.round_id}] Backtest loop finished: end_block={current_block}, "
        f"steps={step_count}, refused={refused_count}, completed_with_rebalances={completed_count}"
    )

    # Build result per miner: backtest and score
    results: Dict[int, Dict[str, Any]] = {}
    for uid in miner_uids:
        if uid in per_miner_refused:
            results[uid] = {
                "accepted": False,
                "refusal_reason": per_miner_refused[uid],
                "rebalance_history": [],
                "final_positions": [
                    p.dict() for p in per_miner_positions[uid] if hasattr(p, "dict")
                ],
                "performance_metrics": {},
                "score": 0.0,
                "total_query_time_ms": per_miner_query_time_ms.get(uid, 0),
            }
            continue
        history = per_miner_history[uid]
        if not history:
            results[uid] = {
                "accepted": True,
                "refusal_reason": None,
                "rebalance_history": [],
                "final_positions": [
                    p.dict() for p in per_miner_positions[uid] if hasattr(p, "dict")
                ],
                "performance_metrics": {},
                "score": 0.0,
                "total_query_time_ms": per_miner_query_time_ms.get(uid, 0),
            }
            continue
        performance_metrics = await backtester.evaluate_positions_performance(
            job.pair_address,
            history,
            start_block,
            current_block,
            initial_inventory,
            job.fee_rate,
        )
        miner_score_val = await Scorer.score_pol_strategy(metrics=performance_metrics)
        serialized_history = [_serialize_history_item(h) for h in history]
        serialized_metrics = _serialize_metrics(performance_metrics)
        final_positions_ser = [
            p.dict() for p in per_miner_positions[uid] if hasattr(p, "dict")
        ]
        results[uid] = {
            "accepted": True,
            "refusal_reason": None,
            "rebalance_history": serialized_history,
            "final_positions": final_positions_ser,
            "performance_metrics": serialized_metrics,
            "score": miner_score_val,
            "total_query_time_ms": per_miner_query_time_ms.get(uid, 0),
        }
        logger.info(
            f"Backtest complete for miner {uid}: {len(history)} rebalances, "
            f"score={miner_score_val}"
        )

    accepted_uids = [uid for uid in miner_uids if results[uid]["accepted"]]
    scores_str = ", ".join(f"{uid}={results[uid]['score']:.4f}" for uid in accepted_uids[:5])
    if len(accepted_uids) > 5:
        scores_str += f", ... ({len(accepted_uids)} total)"
    logger.info(
        f"[ROUND={round_.round_id}] Parallel evaluation done: "
        f"accepted={len(accepted_uids)}, refused={refused_count}, scores=[{scores_str}]"
    )
    return results


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
    rebalance_history: List[Dict] = []
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
                                pos.tick_lower,
                                pos.tick_upper,
                                rebalance_price,
                                int(pos.allocation0),
                                int(pos.allocation1),
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

"""
Execute strategy on-chain via executor bot HTTP API.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from validator.models.job import Job, Round

logger = logging.getLogger(__name__)


async def execute_strategy_onchain(
    job_repository,
    config: Dict[str, Any],
    job: Job,
    round_obj: Round,
    miner_uid: int,
    rebalance_history: List[Dict],
    *,
    timeout: int = 30,
) -> Dict[str, Any]:
    """
    Send strategy to executor bot and record live execution.

    Returns:
        Dict with success, execution_id, tx_hash, error.
    """
    executor_url = config.get("executor_bot_url")
    if not executor_url:
        logger.warning("No executor bot URL configured")
        return {
            "success": False,
            "execution_id": None,
            "tx_hash": None,
            "error": "No executor bot URL configured",
        }

    final_positions = (
        rebalance_history[-1]["new_positions"] if rebalance_history else []
    )
    positions = []
    for pos in final_positions:
        if hasattr(pos, "tick_lower"):
            positions.append({
                "tick_lower": pos.tick_lower,
                "tick_upper": pos.tick_upper,
                "allocation0": pos.allocation0,
                "allocation1": pos.allocation1,
            })
        elif isinstance(pos, dict):
            positions.append(pos)

    # Refresh is_staked from DB so toggles take effect without restart.
    try:
        await job.refresh_from_db(fields=["is_staked"])
    except Exception as e:
        logger.debug(f"is_staked refresh failed: {e}")

    payload = {
        "api_key": config.get("executor_bot_api_key"),
        "job_id": job.job_id,
        "sn_liquidity_manager_address": job.sn_liquidity_manager_address,
        "pair_address": job.pair_address,
        "positions": positions,
        "round_id": round_obj.round_id,
        "miner_uid": miner_uid,
        "has_staking_enabled": bool(getattr(job, "is_staked", False)),
    }

    logger.info(
        f"Executing live strategy: job={job.job_id} round={round_obj.round_id} "
        f"winner_miner_uid={miner_uid} positions={len(positions)} "
        f"detail={positions}"
    )

    execution_id = None
    tx_hash = None
    error = None

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{executor_url.rstrip('/')}/execute_strategy",
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=float(timeout),
            )

        if response.status_code == 200:
            logger.info(
                f"Successfully sent strategy to executor bot for round {round_obj.round_id}, "
                f"miner {miner_uid}"
            )
            try:
                data = response.json()
                tx_hash = data.get("tx_hash")
                error = data.get("error")
                if error:
                    logger.warning(f"Executor bot returned error in response: {error}")
            except Exception as json_err:
                logger.warning(f"Failed to parse executor response as JSON: {json_err}")

            try:
                ex = await job_repository.create_live_execution(
                    round_id=round_obj.round_id,
                    job_id=job.job_id,
                    miner_uid=miner_uid,
                    strategy_data={"positions": positions},
                    tx_hash=tx_hash,
                )
                execution_id = ex.execution_id
                logger.info(
                    f"Live execution recorded: execution_id={execution_id} "
                    f"job={job.job_id} round={round_obj.round_id} miner_uid={miner_uid} "
                    f"tx_hash={tx_hash or 'pending'}"
                )
                if error:
                    ex.tx_status = "failed"
                    ex.actual_performance = {"error": error}
                    await ex.save()
                    logger.warning(f"Live execution {execution_id} marked as failed: {error}")
            except Exception as db_err:
                logger.error(f"Failed to create live execution record: {db_err}", exc_info=True)
                execution_id = None
                error = f"Database error: {str(db_err)}"

            return {
                "success": error is None,
                "execution_id": execution_id,
                "tx_hash": tx_hash,
                "error": error,
            }

        err_msg = f"Executor bot returned status {response.status_code}"
        try:
            if response.text:
                err_msg += f": {response.text}"
        except Exception as e:
            logger.error(f"Failed to read response.text: {str(e)}")
        logger.error(
            f"Executor bot execution failed: {err_msg} "
            f"(round={round_obj.round_id}, miner={miner_uid})"
        )
        eid = await _create_failed_execution_async(
            job_repository, job, round_obj, miner_uid, positions, err_msg
        )
        return {"success": False, "execution_id": eid, "tx_hash": None, "error": err_msg}

    except httpx.HTTPError as e:
        err_msg = f"HTTP client error: {str(e)}"
        logger.error(
            f"Failed to send strategy to executor bot: {err_msg} "
            f"(round={round_obj.round_id}, miner={miner_uid})",
            exc_info=True,
        )
        eid = await _create_failed_execution_async(
            job_repository, job, round_obj, miner_uid, positions, err_msg
        )
        return {"success": False, "execution_id": eid, "tx_hash": None, "error": err_msg}
    except Exception as e:
        err_msg = f"Unexpected error: {str(e)}"
        logger.error(
            f"Unexpected error sending strategy to executor bot: {err_msg} "
            f"(round={round_obj.round_id}, miner={miner_uid})",
            exc_info=True,
        )
        eid = await _create_failed_execution_async(
            job_repository, job, round_obj, miner_uid, positions, err_msg
        )
        return {"success": False, "execution_id": eid, "tx_hash": None, "error": err_msg}


async def execute_swap_onchain(
    config: Dict[str, Any],
    *,
    liquidity_manager_address: str,
    ak_address: str,
    amount_in: int,
    is_buy: bool,
    round_id: str,
    sqrt_price_limit: int = 0,
    min_amount_out: int = 0,
    miner_uid: Optional[int] = None,
    timeout: int = 180,
) -> Dict[str, Any]:
    """
    Send a swap to the executor bot's /execute_swap endpoint.

    Wraps SnLiquidityManager.swap into a Safe multisend tx. amount_in is read
    from the AK's stash inside the vault; the bot serializes this with
    /execute_strategy via a single in-process lock.

    Returns:
        Dict with success, tx_hash, safe_tx_hash, round_id, error.
    """
    executor_url = config.get("executor_bot_url")
    if not executor_url:
        logger.warning("No executor bot URL configured")
        return {
            "success": False,
            "tx_hash": None,
            "safe_tx_hash": None,
            "round_id": round_id,
            "error": "No executor bot URL configured",
        }

    payload = {
        "api_key": config.get("executor_bot_api_key"),
        "liquidity_manager_address": liquidity_manager_address,
        "ak_address": ak_address,
        "amount_in": str(amount_in),
        "is_buy": bool(is_buy),
        "sqrt_price_limit": str(sqrt_price_limit),
        "min_amount_out": str(min_amount_out),
        "round_id": round_id,
    }
    if miner_uid is not None:
        payload["miner_uid"] = miner_uid

    logger.info(
        f"Executing swap: vault={liquidity_manager_address} ak={ak_address} "
        f"amount_in={amount_in} is_buy={is_buy} sqrt_price_limit={sqrt_price_limit} "
        f"min_amount_out={min_amount_out} round_id={round_id}"
    )

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{executor_url.rstrip('/')}/execute_swap",
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=float(timeout),
            )

        if response.status_code == 200:
            try:
                data = response.json()
            except Exception as json_err:
                logger.error(f"Swap response not JSON: {json_err}: {response.text}")
                return {
                    "success": False,
                    "tx_hash": None,
                    "safe_tx_hash": None,
                    "round_id": round_id,
                    "error": f"Invalid JSON in response: {json_err}",
                }
            tx_hash = data.get("tx_hash")
            safe_tx_hash = data.get("safe_tx_hash")
            error = data.get("error")
            success = bool(data.get("success")) and error is None
            if success:
                logger.info(
                    f"Swap recorded: round_id={round_id} tx_hash={tx_hash} "
                    f"safe_tx_hash={safe_tx_hash}"
                )
            else:
                logger.warning(
                    f"Swap reported failure: round_id={round_id} error={error}"
                )
            return {
                "success": success,
                "tx_hash": tx_hash,
                "safe_tx_hash": safe_tx_hash,
                "round_id": round_id,
                "error": error,
            }

        err_msg = f"Executor bot returned status {response.status_code}"
        try:
            if response.text:
                err_msg += f": {response.text}"
        except Exception as e:
            logger.error(f"Failed to read response.text: {e}")
        logger.error(
            f"Swap execution failed: {err_msg} round_id={round_id} "
            f"vault={liquidity_manager_address}"
        )
        return {
            "success": False,
            "tx_hash": None,
            "safe_tx_hash": None,
            "round_id": round_id,
            "error": err_msg,
        }

    except httpx.HTTPError as e:
        err_msg = f"HTTP client error: {e}"
        logger.error(
            f"Failed to send swap to executor bot: {err_msg} round_id={round_id}",
            exc_info=True,
        )
        return {
            "success": False,
            "tx_hash": None,
            "safe_tx_hash": None,
            "round_id": round_id,
            "error": err_msg,
        }
    except Exception as e:
        err_msg = f"Unexpected error: {e}"
        logger.error(
            f"Unexpected error sending swap to executor bot: {err_msg} "
            f"round_id={round_id}",
            exc_info=True,
        )
        return {
            "success": False,
            "tx_hash": None,
            "safe_tx_hash": None,
            "round_id": round_id,
            "error": err_msg,
        }


async def _create_failed_execution_async(
    job_repository, job, round_obj, miner_uid, positions, error_msg: str
):
    try:
        ex = await job_repository.create_live_execution(
            round_id=round_obj.round_id,
            job_id=job.job_id,
            miner_uid=miner_uid,
            strategy_data={"positions": positions},
            tx_hash=None,
        )
        ex.tx_status = "failed"
        ex.actual_performance = {"error": error_msg}
        await ex.save()
        return ex.execution_id
    except Exception as db_err:
        logger.error(f"Failed to create failed execution record: {db_err}", exc_info=True)
        return None

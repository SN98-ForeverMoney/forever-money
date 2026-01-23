"""
Async Round Orchestrator for SN98 ForeverMoney Validator.

Fully async implementation using:
- Tortoise ORM for database
- RebalanceQuery-only protocol (no StrategyRequest)
- Validator-generated initial positions
"""
import logging
import asyncio
from typing import List, Dict, Optional
from datetime import datetime, timezone
import time

import bittensor as bt
import requests

from protocol.synapses import RebalanceQuery
from protocol.models import Position, Inventory
from validator.services.backtester import BacktesterService
from validator.utils.web3 import AsyncWeb3Helper
from validator.utils.math import UniswapV3Math
from validator.repositories.pool import PoolDataDB
from validator.services.liqmanager import SnLiqManagerService
from validator.repositories.job import JobRepository
from validator.models.job import Job, Round, RoundType
from validator.services.scorer import Scorer

logger = logging.getLogger(__name__)


class AsyncRoundOrchestrator:
    """
    Orchestrates evaluation and live rounds for multiple jobs concurrently.

    All operations are async.
    """

    def __init__(
        self,
        job_repository: JobRepository,
        dendrite: bt.Dendrite,
        metagraph: bt.Metagraph,
        config: Dict,
    ):
        """
        Initialize the async round orchestrator.

        Args:
            job_repository: Async job manager instance
            dendrite: Bittensor dendrite for querying miners
            metagraph: Bittensor metagraph
            config: Configuration dictionary
        """
        self.job_repository = job_repository
        self.dendrite = dendrite
        self.metagraph = metagraph
        self.config = config

        # Track round numbers per job
        self.round_numbers: Dict[str, Dict[str, int]] = {}

        # Rebalance check frequency (every N blocks)
        self.rebalance_check_interval = config.get("rebalance_check_interval", 100)
        self.backtester = BacktesterService(PoolDataDB())

    async def _initialize_round_numbers(self, job: Job):
        """
        Initialize round numbers from database for a job.

        Gets the highest round number for each round type from the database
        to handle validator restarts gracefully.

        Args:
            job: Job to initialize round numbers for
        """
        # Get highest round number for evaluation rounds
        eval_round = await Round.filter(
            job=job,
            round_type=RoundType.EVALUATION
        ).order_by('-round_number').first()

        # Get highest round number for live rounds
        live_round = await Round.filter(
            job=job,
            round_type=RoundType.LIVE
        ).order_by('-round_number').first()

        self.round_numbers[job.job_id] = {
            "evaluation": eval_round.round_number if eval_round else 0,
            "live": live_round.round_number if live_round else 0,
        }

        logger.info(
            f"Initialized round numbers for job {job.job_id}: "
            f"evaluation={self.round_numbers[job.job_id]['evaluation']}, "
            f"live={self.round_numbers[job.job_id]['live']}"
        )

    async def run_job_continuously(self, job: Job):
        """
        Run a job continuously with dual-mode rounds.

        Args:
            job: Job to run
        """
        logger.info(f"Starting continuous operation for job {job.job_id}")
        # Initialize round counters from database (handles restarts)
        if job.job_id not in self.round_numbers:
            await self._initialize_round_numbers(job)

        while True:
            try:
                # Run evaluation and live rounds concurrently
                await asyncio.gather(
                    self.run_evaluation_round(job),
                    self.run_live_round(job),
                    # return_exceptions=True,
                )

                # Wait before next round
                logger.info(
                    f"Job {job.job_id}: Sleeping for {job.round_duration_seconds}s"
                )
                await asyncio.sleep(job.round_duration_seconds)

            except Exception as e:
                logger.error(f"Error in job {job.job_id}: {e}", exc_info=True)
                await asyncio.sleep(60)

    async def run_evaluation_round(self, job: Job):
        """
        Run an evaluation round for a job.

        Steps:
        1. Get latest block as target
        2. Generate initial positions (validator-generated)
        3. Run backtest, querying miners at rebalance checkpoints
        4. Score all miners
        5. Select winner

        Args:
            job: Job to run evaluation for
        """
        # Get active miners
        liq_manager = SnLiqManagerService(
            job.chain_id, job.sn_liquidity_manager_address, job.pair_address,
        )
        active_uids = [
            uid for uid in range(len(self.metagraph.S)) if self.metagraph.S[uid] > 0
        ]
        if len(active_uids) == 0:
            logger.warning("No active miners found.")
            return

        self.round_numbers[job.job_id]["evaluation"] += 1
        round_number = self.round_numbers[job.job_id]["evaluation"]

        logger.info(f"=" * 60)
        logger.info(f"Starting EVALUATION round #{round_number} for job {job.job_id}")
        logger.info(f"=" * 60)

        # Get target block
        current_block = await self._get_latest_block(job.chain_id)
        # Create round
        round_obj = await self.job_repository.create_round(
            job=job,
            round_type=RoundType.EVALUATION,
            round_number=round_number,
            start_block=current_block,
        )

        # Get inventory from SNLiquidityManager contract
        inventory = await liq_manager.get_inventory()

        # Get initial positions from on-chain
        initial_positions = await liq_manager.get_current_positions()
        logger.info(f"Loaded {len(initial_positions)} initial positions from on-chain")

        # Run backtest for each miner, querying them at rebalance checkpoints
        scores = await self._evaluate_miners(
            job=job,
            round_=round_obj,
            active_uids=active_uids,
            initial_positions=initial_positions,
            start_block=current_block,
            inventory=inventory,
        )

        # Select winner
        winner = self._select_winner(scores)
        if winner:
            logger.info(
                f"Evaluation round {round_number} winner: Miner {winner['miner_uid']} "
                f"(Score: {winner['score']:.4f})"
            )
        else:
            logger.warning(f"No winner for evaluation round {round_number}")

        # Complete round
        await self.job_repository.complete_round(
            round_id=round_obj.round_id,
            winner_uid=winner["miner_uid"] if winner else None,
            performance_data={"scores": {str(k): v for k, v in scores.items()}},
        )

        logger.info(f"Completed evaluation round {round_number}")

    async def run_live_round(self, job: Job):
        """
        Run a live round for a job.

        Steps:
        1. Get previous evaluation winner
        2. Check if eligible (7+ days participation)
        3. Get initial positions
        4. Query winner for rebalancing decisions
        5. Execute on-chain via executor bot
        6. Evaluate actual performance
        7. Update live scores

        Args:
            job: Job to run live round for
        """
        # 1. Get previous evaluation winner
        winner_uid = await self.job_repository.get_previous_winner(job.job_id)
        if winner_uid is None:
            logger.info(f"No previous winner for job {job.job_id}, skipping live round")
            return

        # 2. Check eligibility
        miner_score = await self.job_repository.get_eligible_miners(job.job_id)
        # Check if winner is in eligible list
        is_eligible = any(s.miner_uid == winner_uid for s in miner_score)
        if not is_eligible:
            logger.info(f"Miner {winner_uid} not eligible for live round yet")
            return

        logger.info(f"=" * 60)
        logger.info(f"Starting LIVE round for job {job.job_id} with Miner {winner_uid}")
        logger.info(f"=" * 60)

        self.round_numbers[job.job_id]["live"] += 1
        round_number = self.round_numbers[job.job_id]["live"]

        # Get target block
        current_block = await self._get_latest_block(job.chain_id)
        
        # Create round
        round_obj = await self.job_repository.create_round(
            job=job,
            round_type=RoundType.LIVE,
            round_number=round_number,
            start_block=current_block,
        )

        liq_manager = SnLiqManagerService(
            job.chain_id, job.sn_liquidity_manager_address, job.pair_address,
        )

        # Get inventory from SNLiquidityManager contract
        inventory = await liq_manager.get_inventory()

        # Get initial positions from on-chain
        initial_positions = await liq_manager.get_current_positions()
        
        # Run live execution loop
        result = await self._run_with_miner_for_live(
            miner_uid=winner_uid,
            job=job,
            round_=round_obj,
            initial_positions=initial_positions,
            start_block=current_block,
            initial_inventory=inventory,
            rebalance_check_interval=self.rebalance_check_interval,
        )

        # Update live score
        if result["accepted"]:
            execution_failures = result.get("execution_failures", 0)
            execution_results = result.get("execution_results", [])
            total_executions = len(execution_results)
            
            # Check if all executions failed - if so, revert score update
            if total_executions > 0 and execution_failures == total_executions:
                logger.error(
                    f"All {total_executions} executions failed for miner {winner_uid} "
                    f"in live round {round_number}. Not updating score."
                )
                # Don't update score if all executions failed
                # The round is still marked as completed but with no score update
            else:
                # For live rounds, we might want to weight the score differently or use actual PnL
                # For now, we use the same simulated metric but based on real execution path
                live_score = result["score"]
                
                # If some executions failed, log a warning but still update score
                if execution_failures > 0:
                    logger.warning(
                        f"Miner {winner_uid} had {execution_failures}/{total_executions} "
                        f"execution failures in live round {round_number}. "
                        f"Score may be inaccurate."
                    )
                
                await self.job_repository.update_miner_score(
                    job_id=job.job_id,
                    miner_uid=winner_uid,
                    miner_hotkey=self.metagraph.hotkeys[winner_uid],
                    live_score=live_score,
                    round_type=RoundType.LIVE,
                )
            
            # Save rebalance decisions (even for live, even if executions failed)
            await self.job_repository.save_rebalance_decision(
                round_id=round_obj.round_id,
                job_id=job.job_id,
                miner_uid=winner_uid,
                miner_hotkey=self.metagraph.hotkeys[winner_uid],
                accepted=True,
                rebalance_data=result["rebalance_history"],
                refusal_reason=None,
                response_time_ms=result.get("total_query_time_ms", 0),
            )
        else:
            logger.warning(f"Miner {winner_uid} failed/refused live round: {result.get('refusal_reason')}")

        # Complete round
        await self.job_repository.complete_round(
            round_id=round_obj.round_id,
            winner_uid=winner_uid if result["accepted"] else None,
            performance_data={"score": result.get("score", 0)},
        )
        
        logger.info(f"Completed LIVE round {round_number}")

    async def _run_with_miner_for_live(
        self,
        miner_uid: int,
        job: Job,
        round_: Round,
        initial_positions: List[Position],
        start_block: int,
        initial_inventory: Inventory,
        rebalance_check_interval: int = 50,
    ) -> Dict:
        """
        Run live round loop, executing decisions on-chain.
        
        Returns:
            Dict with:
                - accepted: bool
                - score: float
                - rebalance_history: List[Dict]
                - total_query_time_ms: int
                - execution_failures: int - Number of failed executions
                - execution_results: List[Dict] - Execution results
        """
        liq_manager = SnLiqManagerService(
            job.chain_id, job.sn_liquidity_manager_address, job.pair_address,
        )
        
        # Track state
        current_positions, current_inventory = initial_positions, initial_inventory
        rebalance_history = [{
            "block": start_block - 1,
            "new_positions": initial_positions,
            "inventory": initial_inventory
        }]
        total_query_time_ms = 0
        rebalances_so_far = 0
        execution_failures = 0
        execution_results = []
        
        current_block = start_block
        
        while round_.round_deadline >= datetime.now(timezone.utc):
            # Check rebalance interval
            if (current_block - start_block) % rebalance_check_interval == 0:
                price_at_query = await liq_manager.get_current_price()
                start_query = time.time()
                
                response = await self._query_miner_for_rebalance(
                    miner_uid=miner_uid,
                    job_id=job.job_id,
                    sn_liquidity_manager_address=job.sn_liquidity_manager_address,
                    pair_address=job.pair_address,
                    round_id=round_.round_id,
                    round_type=round_.round_type,
                    block_number=current_block,
                    current_price=price_at_query,
                    current_positions=current_positions,
                    inventory=current_inventory,
                    rebalances_so_far=rebalances_so_far,
                )
                
                query_time_ms = int((time.time() - start_query) * 1000)
                total_query_time_ms += query_time_ms
                
                if response and response.accepted and response.desired_positions is not None:
                    # Check if positions changed
                    # (Simple check, ideally compare sets/hashes)
                    is_diff = False
                    if len(response.desired_positions) != len(current_positions):
                        is_diff = True
                    else:
                        # Deep compare
                        pass # Assuming always rebalance if sent? Or simple check
                        # For now assume if they sent positions, they want to set them
                        # But we should optimize gas.
                        # Let's assume if it's identical we skip.
                        # For MVP, execute every time miner returns positions? 
                        # Or let miner return None/Empty if no change?
                        # Protocol says: "If desired_positions != current_positions: Rebalance"
                        # We'll rely on miner to be smart, or check strict equality here.
                        pass

                    # Execute on-chain
                    execution_result = await self._execute_strategy_onchain(
                        job=job,
                        round_obj=round_,
                        miner_uid=miner_uid,
                        rebalance_history=rebalance_history + [{
                            "new_positions": response.desired_positions
                        }]
                    )
                    
                    # Track execution result
                    execution_results.append({
                        "block": current_block,
                        "success": execution_result["success"],
                        "execution_id": execution_result.get("execution_id"),
                        "tx_hash": execution_result.get("tx_hash"),
                        "error": execution_result.get("error")
                    })
                    
                    if execution_result["success"]:
                        # Record the rebalance in our local history for scoring
                        # In live mode, we should ideally fetch the NEW inventory/positions from chain
                        # after execution. But execution is async via bot.
                        # We assume execution succeeds for simulation purposes?
                        # Or we wait?
                        # For MVP, we update local state assuming success.
                        
                        # Recalculate inventory usage locally
                        rebalance_price = await liq_manager.get_current_price()
                        total_amount_0_placed, total_amount_1_placed = 0, 0
                        for position in response.desired_positions:
                            (_, a0, a1) = UniswapV3Math.position_liquidity_and_used_amounts(
                                position.tick_lower, position.tick_upper,
                                int(position.allocation0), int(position.allocation1),
                                rebalance_price
                            )
                            total_amount_0_placed += a0
                            total_amount_1_placed += a1
                            
                        # Update inventory (simplified)
                        # In reality, inventory changes due to fees/swaps.
                        # We should probably re-fetch inventory from chain next loop.
                        # But for scoring consistency, we track logical inventory.
                        amount_0_int = int(initial_inventory.amount0) - total_amount_0_placed
                        amount_1_int = int(initial_inventory.amount1) - total_amount_1_placed
                        
                        current_inventory = Inventory(
                            amount0=str(max(0, amount_0_int)), 
                            amount1=str(max(0, amount_1_int))
                        )
                        
                        rebalance_history.append({
                            "block": current_block,
                            "price": rebalance_price,
                            "price_in_query": price_at_query,
                            "old_positions": current_positions,
                            "new_positions": response.desired_positions,
                            "inventory": current_inventory,
                            "execution_id": execution_result.get("execution_id"),
                            "tx_hash": execution_result.get("tx_hash"),
                        })
                        
                        current_positions = response.desired_positions
                        rebalances_so_far += 1
                    else:
                        # Execution failed - log and continue
                        execution_failures += 1
                        logger.error(
                            f"Failed to execute strategy on-chain for miner {miner_uid} "
                            f"at block {current_block}: {execution_result.get('error')}"
                        )
                        # Don't update positions if execution failed
                        # The rebalance will be retried on next interval if miner still wants it
                        
            else:
                await asyncio.sleep(1)
            
            current_block = await self._get_latest_block(job.chain_id)

        # Calculate score (using same backtester logic for consistency)
        performance_metrics = await self.backtester.evaluate_positions_performance(
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
            "execution_results": execution_results
        }

    async def _evaluate_miners(
        self,
        job: Job,
        round_: Round,
        active_uids: List[int],
        initial_positions: List[Position],
        start_block: int,
        inventory: Inventory,
    ) -> Dict[int, Dict]:
        """
        Evaluate all active miners by running backtests.

        Args:
            job: Job context
            round_: Round object
            active_uids: List of active miner UIDs
            initial_positions: Initial positions
            start_block: Start block
            inventory: Inventory

        Returns:
            Dict mapping miner_uid to score data
        """
        tasks = []
        for uid in active_uids:
            task = self._run_with_miner_for_evaluation(
                miner_uid=uid,
                job=job,
                round_=round_,
                initial_positions=initial_positions,
                start_block=start_block,
                initial_inventory=inventory,
                rebalance_check_interval=self.rebalance_check_interval,
            )
            tasks.append(task)

        # Run all backtests concurrently
        results = await asyncio.gather(*tasks)

        # Process results
        scores = {}
        for uid, result in zip(active_uids, results):
            if result["accepted"]:
                scores[uid] = {
                    "hotkey": self.metagraph.hotkeys[uid],
                    "score": result["score"],
                    "result": result,
                }

                # Save rebalance decisions
                await self.job_repository.save_rebalance_decision(
                    round_id=round_.round_id,
                    job_id=job.job_id,
                    miner_uid=uid,
                    miner_hotkey=self.metagraph.hotkeys[uid],
                    accepted=True,
                    rebalance_data=result["rebalance_history"],
                    refusal_reason=None,
                    response_time_ms=result.get("total_query_time_ms", 0),
                )
            else:
                # Miner refused
                logger.info(f"Miner {uid} refused job: {result.get('refusal_reason')}")
                await self.job_repository.save_rebalance_decision(
                    round_id=round_.round_id,
                    job_id=job.job_id,
                    miner_uid=uid,
                    miner_hotkey=self.metagraph.hotkeys[uid],
                    accepted=False,
                    rebalance_data=None,
                    refusal_reason=result.get("refusal_reason"),
                    response_time_ms=0,
                )

        return scores

    async def _run_with_miner_for_evaluation(
        self,
        miner_uid: int,
        job: Job,
        round_: Round,
        initial_positions: List[Position],
        start_block: int,
        initial_inventory: Inventory,
        rebalance_check_interval: int = 50,
    ) -> Dict:
        """
        Run backtest, querying miner for rebalancing decisions.

        Args:
            miner_uid: Miner UID to query
            job: Job
            round_: The round object
            initial_positions: Initial positions to start with
            start_block: Start block
            initial_inventory: Available inventory
            rebalance_check_interval: Check for rebalance every N blocks

        Returns:
            Dict with:
                - accepted: Whether miner accepted the job
                - refusal_reason: Reason if refused
                - rebalance_history: List of rebalancing decisions
                - final_positions: Final positions
                - performance_metrics: PnL, fees, etc.
                - total_query_time_ms: Total time spent querying miner
        """
        liq_manager = SnLiqManagerService(
            job.chain_id, job.sn_liquidity_manager_address, job.pair_address,
        )
        logger.info(f"[ROUND={round_.round_id}] Running backtest for miner {miner_uid}")

        # Track state
        current_positions, current_inventory = initial_positions, initial_inventory
        # Initialize history with starting state (at block before start to cover start_block)
        rebalance_history = [{
            "block": start_block - 1,
            "new_positions": initial_positions,
            "inventory": initial_inventory
        }]
        total_query_time_ms = 0
        rebalances_so_far = 0

        # Simulate block by block (with checkpoints)
        current_block = start_block
        while round_.round_deadline >= datetime.now(timezone.utc):
            # Check if we should query miner for rebalance
            if (current_block - start_block) % rebalance_check_interval == 0:
                # Query miner
                logger.debug(f"Querying miner {miner_uid} at block {current_block}")
                price_at_query = await liq_manager.get_current_price()
                start_query = time.time()
                response = await self._query_miner_for_rebalance(
                    miner_uid=miner_uid,
                    job_id=job.job_id,
                    sn_liquidity_manager_address=job.sn_liquidity_manager_address,
                    pair_address=job.pair_address,
                    round_id=round_.round_id,
                    round_type=round_.round_type,
                    block_number=current_block,
                    current_price=price_at_query,
                    current_positions=current_positions,
                    inventory=current_inventory,
                    rebalances_so_far=rebalances_so_far,
                )

                query_time_ms = int((time.time() - start_query) * 1000)
                total_query_time_ms += query_time_ms

                if response is None:
                    # Timeout or error
                    logger.warning(
                        f"Miner {miner_uid} timeout/error at block {current_block}"
                    )
                    return {
                        "accepted": False,
                        "refusal_reason": "Timeout or error",
                        "rebalance_history": rebalance_history,
                        "final_positions": current_positions,
                        "performance_metrics": {},
                        "total_query_time_ms": total_query_time_ms,
                    }

                if not response.accepted:
                    # Miner refused job
                    logger.info(
                        f"Miner {miner_uid} refused job: {response.refusal_reason}"
                    )
                    return {
                        "accepted": False,
                        "refusal_reason": response.refusal_reason,
                        "rebalance_history": rebalance_history,
                        "final_positions": current_positions,
                        "performance_metrics": {},
                        "total_query_time_ms": total_query_time_ms,
                    }

                if response.desired_positions is not None:
                    # Miner wants to rebalance
                    logger.debug(
                        f"Miner {miner_uid} rebalancing at block {current_block}: "
                        f"{len(response.desired_positions)} positions"
                    )

                    # get price again to simulate real price on-chain
                    # this price is closer to the real one, as execution would happen
                    # after the prediction from the miner
                    rebalance_price = await liq_manager.get_current_price()
                    total_amount_0_placed, total_amount_1_placed = 0, 0
                    for position in response.desired_positions:
                        (
                            _,
                            actual_amount0_used,
                            actual_amount1_used,
                        ) = UniswapV3Math.position_liquidity_and_used_amounts(
                            position.tick_lower,
                            position.tick_upper,
                            int(position.allocation0),
                            int(position.allocation1),
                            rebalance_price,
                        )
                        total_amount_0_placed += actual_amount0_used
                        total_amount_1_placed += actual_amount1_used

                    amount_0_int = int(initial_inventory.amount0) - total_amount_0_placed
                    amount_1_int = int(initial_inventory.amount1) - total_amount_1_placed
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
                        amount0=str(amount_0_int),
                        amount1=str(amount_1_int),
                    )
                    rebalance_history.append(
                        {
                            "block": current_block,
                            "price": rebalance_price,
                            "price_in_query": price_at_query,
                            "old_positions": current_positions,
                            "new_positions": response.desired_positions,
                            "inventory": current_inventory,
                        }
                    )

                    current_positions = response.desired_positions
                    rebalances_so_far += 1
            else:
                await asyncio.sleep(1)
            # Move to next checkpoint
            current_block = await self._get_latest_block(job.chain_id)

        # Calculate performance
        logger.debug(f"Rebalance history: {rebalance_history}")
        performance_metrics = await self.backtester.evaluate_positions_performance(
            job.pair_address,
            rebalance_history,
            start_block,
            current_block,
            initial_inventory,
            job.fee_rate,
        )
        logger.info(
            f"Backtest complete for miner {miner_uid}: "
            f"{len(rebalance_history)} rebalances, "
            f"PnL: {performance_metrics.get('pnl', 0):.4f}"
        )
        miner_score_val = await Scorer.score_pol_strategy(metrics=performance_metrics)
        # calculate the miner score, based on the score their strategy
        # got for this round
        await self.job_repository.update_miner_score(
            job_id=job.job_id,
            miner_uid=miner_uid,
            miner_hotkey=self.metagraph.hotkeys[miner_uid],
            # score here is the score for this particular round
            evaluation_score=miner_score_val,
            round_type=RoundType.EVALUATION,
        )
        await self.job_repository.update_miner_participation(
            job_id=job.job_id, miner_uid=miner_uid, participated=True
        )

        # Serialize for storage
        serialized_history = []
        for item in rebalance_history:
            new_item = item.copy()
            if "inventory" in new_item and hasattr(new_item["inventory"], "dict"):
                new_item["inventory"] = new_item["inventory"].dict()
            if "new_positions" in new_item:
                new_item["new_positions"] = [p.dict() for p in new_item["new_positions"] if hasattr(p, "dict")]
            if "old_positions" in new_item:
                new_item["old_positions"] = [p.dict() for p in new_item["old_positions"] if hasattr(p, "dict")]
            serialized_history.append(new_item)

        serialized_metrics = performance_metrics.copy()
        if "initial_inventory" in serialized_metrics and hasattr(serialized_metrics["initial_inventory"], "dict"):
            serialized_metrics["initial_inventory"] = serialized_metrics["initial_inventory"].dict()
        if "final_inventory" in serialized_metrics and hasattr(serialized_metrics["final_inventory"], "dict"):
            serialized_metrics["final_inventory"] = serialized_metrics["final_inventory"].dict()

        return {
            "accepted": True,
            "refusal_reason": None,
            "rebalance_history": serialized_history,
            "final_positions": [p.dict() for p in current_positions if hasattr(p, "dict")],
            "performance_metrics": serialized_metrics,
            "score": miner_score_val,
            "total_query_time_ms": total_query_time_ms,
        }

    async def _query_miner_for_rebalance(
        self,
        miner_uid: int,
        job_id: str,
        sn_liquidity_manager_address: str,
        pair_address: str,
        round_id: str,
        round_type: str,
        block_number: int,
        current_price: float,
        current_positions: List[Position],
        inventory: Inventory,
        rebalances_so_far: int,
    ) -> Optional[RebalanceQuery]:
        """
        Query a single miner for rebalancing decision.

        Args:
            miner_uid: Miner UID
            job_id: Job identifier
            sn_liquidity_manager_address: Vault address
            pair_address: Pool address
            round_id: Round identifier
            round_type: 'evaluation' or 'live'
            block_number: Current block
            current_price: Current price
            current_positions: Current positions
            inventory: Available inventory
            rebalances_so_far: Number of rebalances so far

        Returns:
            RebalanceQuery response or None if timeout
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
            inventory_remaining={
                "amount0": inventory.amount0,
                "amount1": inventory.amount1,
            },
            rebalances_so_far=rebalances_so_far,
        )

        try:
            responses = await self.dendrite(
                axons=[self.metagraph.axons[miner_uid]],
                synapse=synapse,
                timeout=5,  # 5 second timeout per query
                deserialize=True,
            )
            logger.debug(f"Miner response: {responses[0] if responses else 'None'}")

            response = responses[0] if responses else None

            if response and hasattr(response, "accepted"):
                return response

            logger.debug(f"Miner refused or failed. Refusal reason: {response.refusal_reason if response else 'No response'}")
            return None

        except Exception as e:
            logger.error(f"Error querying miner {miner_uid}: {e}")
            return None

    async def _execute_strategy_onchain(
        self, job: Job, round_obj: Round, miner_uid: int, rebalance_history: List[Dict]
    ) -> Dict[str, any]:
        """
        Execute strategy on-chain via executor bot.

        Args:
            job: Job context
            round_obj: Round object
            miner_uid: Miner UID
            rebalance_history: List of rebalancing decisions

        Returns:
            Dict with:
                - success: bool - Whether execution was initiated successfully
                - execution_id: Optional[str] - LiveExecution ID if created
                - tx_hash: Optional[str] - Transaction hash if available
                - error: Optional[str] - Error message if failed
        """
        executor_url = self.config.get("executor_bot_url")
        if not executor_url:
            logger.warning("No executor bot URL configured")
            return {
                "success": False,
                "execution_id": None,
                "tx_hash": None,
                "error": "No executor bot URL configured"
            }

        # Get final positions from last rebalance
        final_positions = (
            rebalance_history[-1]["new_positions"] if rebalance_history else []
        )

        # Serialize positions - handle both Position objects and dicts
        positions = []
        for pos in final_positions:
            if hasattr(pos, 'tick_lower'):
                # Position object
                positions.append({
                    "tick_lower": pos.tick_lower,
                    "tick_upper": pos.tick_upper,
                    "allocation0": pos.allocation0,
                    "allocation1": pos.allocation1,
                })
            elif isinstance(pos, dict):
                # Already a dict
                positions.append(pos)

        # Verify payload structure matches Executor Bot expectations
        payload = {
            "api_key": self.config.get("executor_bot_api_key"),
            "job_id": job.job_id,
            "sn_liquidity_manager_address": job.sn_liquidity_manager_address,
            "pair_address": job.pair_address,
            "positions": positions,
            "round_id": round_obj.round_id,
            "miner_uid": miner_uid,
        }

        # Validate required fields
        if not payload.get("api_key"):
            error_msg = "Missing executor_bot_api_key in config"
            logger.error(error_msg)
            return {
                "success": False,
                "execution_id": None,
                "tx_hash": None,
                "error": error_msg
            }

        execution_id = None
        tx_hash = None
        error = None

        try:
            # Use requests with asyncio.to_thread to run synchronously in thread pool
            def make_request():
                return requests.post(
                    f"{executor_url}/execute_strategy",
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=30
                )
            
            response = await asyncio.to_thread(make_request)
            
            if response.status_code == 200:
                logger.info(
                    f"Successfully sent strategy to executor bot for round {round_obj.round_id}, "
                    f"miner {miner_uid}"
                )
                
                # Parse response to get tx details if available
                try:
                    response_data = response.json()
                    tx_hash = response_data.get("tx_hash")
                    error_msg = response_data.get("error")
                    
                    if error_msg:
                        logger.warning(
                            f"Executor bot returned error in response: {error_msg}"
                        )
                        error = error_msg
                except Exception as json_error:
                    logger.warning(
                        f"Failed to parse executor bot response as JSON: {json_error}"
                    )
                
                # Record live execution in DB (even if there's an error message)
                try:
                    execution = await self.job_repository.create_live_execution(
                        round_id=round_obj.round_id,
                        job_id=job.job_id,
                        miner_uid=miner_uid,
                        strategy_data={"positions": positions},
                        tx_hash=tx_hash
                    )
                    execution_id = execution.execution_id
                    
                    # Update execution status based on response
                    if error:
                        execution.tx_status = "failed"
                        execution.actual_performance = {"error": error}
                        await execution.save()
                        logger.warning(
                            f"Live execution {execution_id} marked as failed: {error}"
                        )
                except Exception as db_error:
                    logger.error(
                        f"Failed to create live execution record: {db_error}",
                        exc_info=True
                    )
                    execution_id = None
                    error = f"Database error: {str(db_error)}"
                
                return {
                    "success": error is None,  # Success only if no error
                    "execution_id": execution_id,
                    "tx_hash": tx_hash,
                    "error": error
                }
            else:
                # Non-200 status code
                error_msg = f"Executor bot returned status {response.status_code}"
                try:
                    error_body = response.text
                    if error_body:
                        error_msg += f": {error_body}"
                except Exception:
                    pass
                
                logger.error(
                    f"Executor bot execution failed: {error_msg} "
                    f"(round={round_obj.round_id}, miner={miner_uid})"
                )
                
                # Still create execution record with failed status
                try:
                    execution = await self.job_repository.create_live_execution(
                        round_id=round_obj.round_id,
                        job_id=job.job_id,
                        miner_uid=miner_uid,
                        strategy_data={"positions": positions},
                        tx_hash=None
                    )
                    execution_id = execution.execution_id
                    execution.tx_status = "failed"
                    execution.actual_performance = {"error": error_msg}
                    await execution.save()
                except Exception as db_error:
                    logger.error(
                        f"Failed to create failed execution record: {db_error}",
                        exc_info=True
                    )
                    execution_id = None
                
                return {
                    "success": False,
                    "execution_id": execution_id,
                    "tx_hash": None,
                    "error": error_msg
                }

        except requests.RequestException as e:
            error_msg = f"HTTP client error: {str(e)}"
            logger.error(
                f"Failed to send strategy to executor bot: {error_msg} "
                f"(round={round_obj.round_id}, miner={miner_uid})",
                exc_info=True
            )
            
            # Create execution record with failed status
            try:
                execution = await self.job_repository.create_live_execution(
                    round_id=round_obj.round_id,
                    job_id=job.job_id,
                    miner_uid=miner_uid,
                    strategy_data={"positions": positions},
                    tx_hash=None
                )
                execution_id = execution.execution_id
                execution.tx_status = "failed"
                execution.actual_performance = {"error": error_msg}
                await execution.save()
            except Exception as db_error:
                logger.error(
                    f"Failed to create failed execution record: {db_error}",
                    exc_info=True
                )
                execution_id = None
            
            return {
                "success": False,
                "execution_id": execution_id,
                "tx_hash": None,
                "error": error_msg
            }
        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            logger.error(
                f"Unexpected error sending strategy to executor bot: {error_msg} "
                f"(round={round_obj.round_id}, miner={miner_uid})",
                exc_info=True
            )
            
            # Create execution record with failed status
            try:
                execution = await self.job_repository.create_live_execution(
                    round_id=round_obj.round_id,
                    job_id=job.job_id,
                    miner_uid=miner_uid,
                    strategy_data={"positions": positions},
                    tx_hash=None
                )
                execution_id = execution.execution_id
                execution.tx_status = "failed"
                execution.actual_performance = {"error": error_msg}
                await execution.save()
            except Exception as db_error:
                logger.error(
                    f"Failed to create failed execution record: {db_error}",
                    exc_info=True
                )
            
            return {
                "success": False,
                "execution_id": execution_id,
                "tx_hash": None,
                "error": error_msg
            }

    def _select_winner(self, scores: Dict[int, Dict]) -> Optional[Dict]:
        """Select winner from scores."""
        if not scores:
            return None

        sorted_scores = sorted(
            [(uid, data) for uid, data in scores.items()],
            key=lambda x: x[1]["score"],
            reverse=True,
        )

        winner_uid, winner_data = sorted_scores[0]

        return {
            "miner_uid": winner_uid,
            "hotkey": winner_data["hotkey"],
            "score": winner_data["score"],
        }

    async def _get_latest_block(self, chain_id: int) -> int:
        """Get latest block from chain."""
        latest_block = await AsyncWeb3Helper.make_web3(chain_id).web3.eth.block_number
        return latest_block

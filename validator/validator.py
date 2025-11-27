"""
Main Validator implementation for SN98 ForeverMoney.
"""
import logging
import time
import uuid
import json
from typing import List, Dict, Any, Optional, Iterable
import requests

import bittensor as bt

from validator.models import (
    ValidatorRequest,
    MinerResponse,
    Inventory,
    Metadata,
    Constraints,
    Mode,
    MinerScore
)
from validator.database import PoolDataDB
from validator.backtester import Backtester, DEFAULT_FEE_RATE
from validator.constraints import ConstraintValidator
from validator.scorer import Scorer

logger = logging.getLogger(__name__)


class SN98Validator:
    """
    Main Validator for SN98 ForeverMoney subnet.

    Responsibilities:
    1. Generate and publish round parameters
    2. Poll miners for strategy submissions
    3. Backtest strategies using historical data
    4. Enforce constraints
    5. Score strategies (70% performance + 30% LP alignment)
    6. Publish winning strategy
    """

    def __init__(
        self,
        wallet: bt.wallet,
        subtensor: bt.subtensor,
        metagraph: bt.metagraph,
        db: PoolDataDB,
        config: Dict[str, Any]
    ):
        self.wallet = wallet
        self.subtensor = subtensor
        self.metagraph = metagraph
        self.db = db
        self.config = config

        # Get fee rate from config or use default
        fee_rate = config.get('fee_rate', DEFAULT_FEE_RATE)

        # Initialize components with fee rate
        self.backtester = Backtester(db, fee_rate=fee_rate)
        self.scorer = Scorer(
            performance_weight=config.get('performance_weight', 0.7),
            lp_alignment_weight=config.get('lp_alignment_weight', 0.3),
            top_n_strategies=config.get('top_n_strategies', 3)
        )

        # Vault registry: mapping miner_uid -> vault_address
        # In production, this would be loaded from chain or config
        self.vault_registry: Dict[int, str] = config.get('vault_registry', {})

        logger.info(f"Validator initialized with hotkey: {wallet.hotkey.ss58_address}")
        logger.info(f"Fee rate: {fee_rate:.4f} ({fee_rate*100:.2f}%)")

    def query_test_miner(
        self,
        request: ValidatorRequest,
        test_miner_url: str,
        timeout: int = 30
    ) -> Optional[MinerResponse]:
        """
        Query a test miner directly (bypassing metagraph).

        Args:
            request: ValidatorRequest to send
            test_miner_url: URL of the test miner (e.g., http://localhost:8000)
            timeout: Request timeout in seconds

        Returns:
            MinerResponse or None if failed
        """
        url = f"{test_miner_url.rstrip('/')}/predict_strategy"

        try:
            logger.info(f"Querying test miner at {url}")

            response = requests.post(
                url,
                json=request.model_dump(),
                timeout=timeout,
                headers={'Content-Type': 'application/json'}
            )

            if response.status_code == 200:
                data = response.json()
                miner_response = MinerResponse(**data)
                logger.info(f"Received response from test miner")
                return miner_response
            else:
                logger.warning(
                    f"Test miner returned status {response.status_code}: "
                    f"{response.text}"
                )
                return None

        except requests.exceptions.Timeout:
            logger.warning(f"Test miner request timed out")
            return None
        except requests.exceptions.RequestException as e:
            logger.warning(f"Error querying test miner: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error querying test miner: {e}")
            return None

    def generate_round_request(
        self,
        pair_address: str,
        target_block: int,
        inventory: Optional[Inventory] = None,
        mode: Mode = Mode.INVENTORY
    ) -> ValidatorRequest:
        """
        Generate a round request for miners.

        Args:
            pair_address: Address of the trading pair
            target_block: Target block for strategy prediction
            inventory: Available token inventory
            mode: Operation mode (inventory or position)

        Returns:
            ValidatorRequest object
        """
        round_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"

        constraints = Constraints(
            max_il=self.config.get('max_il', 0.10),
            min_tick_width=self.config.get('min_tick_width', 60),
            max_rebalances=self.config.get('max_rebalances', 4)
        )

        metadata = Metadata(
            round_id=round_id,
            constraints=constraints
        )

        request = ValidatorRequest(
            pairAddress=pair_address,
            chainId=self.config.get('chain_id', 8453),
            target_block=target_block,
            mode=mode,
            inventory=inventory,
            metadata=metadata,
            postgres_access=self.config.get('postgres_access', None)
        )

        logger.info(f"Generated round request: {round_id}")
        return request

    def query_miner(
        self,
        miner_uid: int,
        request: ValidatorRequest,
        timeout: int = 5
    ) -> Optional[MinerResponse]:
        """
        Query a single miner for their strategy.

        Args:
            miner_uid: Miner UID
            request: ValidatorRequest to send
            timeout: Request timeout in seconds

        Returns:
            MinerResponse or None if failed
        """
        # Get miner axon info
        axon = self.metagraph.axons[miner_uid]

        if not axon.is_serving:
            logger.warning(f"Miner {miner_uid} is not serving")
            return None

        # Construct endpoint URL
        # Miners should expose /predict_strategy endpoint
        url = f"http://{axon.ip}:{axon.port}/predict_strategy"

        try:
            logger.info(f"Querying miner {miner_uid} at {url}")

            response = requests.post(
                url,
                json=request.model_dump(),
                timeout=timeout,
                headers={'Content-Type': 'application/json'}
            )

            if response.status_code == 200:
                data = response.json()
                miner_response = MinerResponse(**data)
                logger.info(f"Received response from miner {miner_uid}")
                return miner_response
            else:
                logger.warning(
                    f"Miner {miner_uid} returned status {response.status_code}: "
                    f"{response.text}"
                )
                return None

        except requests.exceptions.Timeout:
            logger.warning(f"Miner {miner_uid} request timed out")
            return None
        except requests.exceptions.RequestException as e:
            logger.warning(f"Error querying miner {miner_uid}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error querying miner {miner_uid}: {e}")
            return None

    def poll_miners(
        self,
        request: ValidatorRequest,
        miner_uids: Optional[List[int]] = None
    ) -> Dict[int, MinerResponse]:
        """
        Poll all active miners for strategies.

        Args:
            request: ValidatorRequest to send
            miner_uids: Optional list of specific miner UIDs to poll

        Returns:
            Dictionary mapping miner_uid to MinerResponse
        """
        if miner_uids is None:
            # Poll all active miners
            miner_uids = [
                uid for uid, axon in enumerate(self.metagraph.axons)
                if axon.is_serving
            ]

        logger.info(f"Polling {len(miner_uids)} miners")

        responses = {}
        for uid in miner_uids:
            response = self.query_miner(uid, request)
            if response:
                responses[uid] = response

        logger.info(f"Received {len(responses)} valid responses")
        return responses

    def evaluate_strategies(
        self,
        miner_responses: Dict[int, MinerResponse],
        request: ValidatorRequest,
        start_block: int,
        end_block: int
    ) -> List[MinerScore]:
        """
        Evaluate all miner strategies through backtesting and scoring.

        Args:
            miner_responses: Strategies from miners
            request: Original request (contains constraints)
            start_block: Starting block for backtest
            end_block: Ending block for backtest

        Returns:
            List of MinerScore objects
        """
        miner_metrics = {}
        constraint_violations = {}

        # Initialize constraint validator
        validator = ConstraintValidator(request.metadata.constraints)

        # Get initial amounts from inventory or use sensible defaults
        if request.inventory:
            initial_amount0 = int(request.inventory.amount0)
            initial_amount1 = int(request.inventory.amount1)
        else:
            # Default: 1 ETH (18 decimals) and 2500 USDC (6 decimals)
            # These represent a ~$5000 position at ~$2500/ETH
            logger.warning("No inventory provided, using default amounts (1 ETH + 2500 USDC)")
            initial_amount0 = 1_000_000_000_000_000_000  # 1 ETH in wei
            initial_amount1 = 2_500_000_000  # 2500 USDC (6 decimals)

        # Get fee rate for backtesting
        fee_rate = self.config.get('fee_rate', DEFAULT_FEE_RATE)

        # Backtest each strategy
        for uid, response in miner_responses.items():
            logger.info(f"Evaluating strategy from miner {uid}")

            # Validate constraints
            is_valid, violations = validator.validate_strategy(response.strategy)

            if not is_valid:
                logger.warning(f"Miner {uid} strategy has constraint violations: {violations}")
                constraint_violations[uid] = violations
                # Still backtest but score will be 0
            else:
                constraint_violations[uid] = []

            # Backtest strategy
            try:
                metrics = self.backtester.backtest_strategy(
                    pair_address=request.pairAddress,
                    strategy=response.strategy,
                    initial_amount0=initial_amount0,
                    initial_amount1=initial_amount1,
                    start_block=start_block,
                    end_block=end_block,
                    fee_rate=fee_rate
                )

                # Validate performance metrics
                perf_valid, perf_violations = validator.validate_performance_metrics(
                    metrics.impermanent_loss,
                    metrics.num_rebalances
                )

                if not perf_valid:
                    constraint_violations[uid].extend(perf_violations)
                    logger.warning(f"Miner {uid} performance violations: {perf_violations}")

                miner_metrics[uid] = metrics

                logger.info(
                    f"Miner {uid} metrics: "
                    f"PnL vs HODL={metrics.net_pnl_vs_hodl:.4f}, "
                    f"Fees={metrics.total_fees_collected:.4f}, "
                    f"IL={metrics.impermanent_loss:.2%}, "
                    f"Rebalances={metrics.num_rebalances}"
                )

            except Exception as e:
                logger.error(f"Error backtesting miner {uid} strategy: {e}")
                constraint_violations[uid].append(f"Backtesting error: {str(e)}")

        # Get LP alignment data (vault fees)
        vault_fees = self._get_vault_fees(
            miner_uids=list(miner_responses.keys()),
            pair_address=request.pairAddress,
            start_block=start_block,
            end_block=end_block
        )

        # Get miner hotkeys (safely handle test miners and missing UIDs)
        miner_hotkeys = self._get_miner_hotkeys(miner_responses.keys())

        # Calculate final scores
        scores = self.scorer.calculate_final_scores(
            miner_metrics=miner_metrics,
            vault_fees=vault_fees,
            miner_hotkeys=miner_hotkeys,
            constraint_violations=constraint_violations
        )

        return scores

    def _get_miner_hotkeys(self, miner_uids: Iterable[int]) -> Dict[int, str]:
        """
        Safely get hotkeys for miner UIDs, handling test miners and missing UIDs.

        Args:
            miner_uids: Iterable of miner UIDs

        Returns:
            Dictionary mapping miner_uid to hotkey string
        """
        hotkeys = {}
        for uid in miner_uids:
            if uid < 0:
                # Test miner
                hotkeys[uid] = f"test_miner_{uid}"
            elif uid < len(self.metagraph.hotkeys):
                hotkeys[uid] = self.metagraph.hotkeys[uid]
            else:
                # Unknown UID
                hotkeys[uid] = f"unknown_{uid}"
        return hotkeys

    def _get_vault_fees(
        self,
        miner_uids: List[int],
        pair_address: str,
        start_block: int,
        end_block: int
    ) -> Dict[int, float]:
        """
        Get LP fees collected by miner vaults.

        This method attempts to:
        1. Look up vault addresses from the vault registry
        2. Query actual fees from the database
        3. Fall back to equal distribution if no vault data available

        Args:
            miner_uids: List of miner UIDs
            pair_address: Pool address
            start_block: Starting block
            end_block: Ending block

        Returns:
            Dictionary mapping miner_uid to total fees
        """
        vault_fees: Dict[int, float] = {}

        # Check if we have vault addresses for any miners
        vault_addresses = []
        uid_to_vault = {}
        for uid in miner_uids:
            vault_addr = self.vault_registry.get(uid)
            if vault_addr:
                vault_addresses.append(vault_addr)
                uid_to_vault[vault_addr.lower().replace('0x', '')] = uid

        # If we have vault addresses, query actual fees
        if vault_addresses:
            try:
                fees_by_vault = self.db.get_miner_vault_fees(
                    vault_addresses=vault_addresses,
                    start_block=start_block,
                    end_block=end_block
                )

                # Get price at end block for converting fees to common denominator
                end_price = self.db.get_price_at_block(pair_address, end_block) or 1.0

                # Map fees back to UIDs
                for vault_addr, fees in fees_by_vault.items():
                    clean_addr = vault_addr.lower().replace('0x', '')
                    if clean_addr in uid_to_vault:
                        uid = uid_to_vault[clean_addr]
                        # Combine fee0 and fee1 into single value (in token1 terms)
                        total_fee = fees.get('fee0', 0) * end_price + fees.get('fee1', 0)
                        vault_fees[uid] = total_fee

                # For miners without vault data, give them 0 fees
                for uid in miner_uids:
                    if uid not in vault_fees:
                        vault_fees[uid] = 0.0

                logger.info(f"Queried vault fees for {len(vault_fees)} miners from database")
                return vault_fees

            except Exception as e:
                logger.warning(f"Error querying vault fees from DB: {e}, falling back to equal distribution")

        # Fallback: Equal LP alignment for all miners
        # This gives everyone equal share of the 30% LP component
        for uid in miner_uids:
            vault_fees[uid] = 1.0

        logger.info(f"Using equal LP alignment scores for {len(miner_uids)} miners (no vault registry)")
        return vault_fees

    def publish_scores(self, scores: List[MinerScore]) -> None:
        """
        Publish scores to the Bittensor network.

        Args:
            scores: List of MinerScore objects
        """
        # Check for dry-run mode
        if self.config.get('dry_run', False):
            logger.info("DRY-RUN: Skipping weight publishing to chain")
            logger.info("DRY-RUN: Scores that would be published:")
            for score in scores:
                logger.info(f"  Miner {score.miner_uid}: {score.final_score:.4f}")
            return

        # Prepare weights for setting
        weights = [0.0] * len(self.metagraph.uids)

        for score in scores:
            if 0 <= score.miner_uid < len(weights):
                weights[score.miner_uid] = score.final_score

        # Normalize weights
        total_weight = sum(weights)
        if total_weight > 0:
            weights = [w / total_weight for w in weights]

        # Set weights on chain
        try:
            self.subtensor.set_weights(
                wallet=self.wallet,
                netuid=self.metagraph.netuid,
                uids=self.metagraph.uids,
                weights=weights,
                wait_for_inclusion=True
            )
            logger.info("Successfully published weights to chain")
        except Exception as e:
            logger.error(f"Error publishing weights: {e}")

    def publish_winning_strategy(
        self,
        winning_score: MinerScore,
        miner_response: MinerResponse
    ) -> None:
        """
        Publish the winning strategy for the Executor Bot.

        Args:
            winning_score: The winning MinerScore
            miner_response: The winning miner's response
        """
        output = {
            'round_timestamp': int(time.time()),
            'winner': {
                'miner_uid': winning_score.miner_uid,
                'miner_hotkey': winning_score.miner_hotkey,
                'final_score': winning_score.final_score,
                'performance_score': winning_score.performance_score,
                'lp_alignment_score': winning_score.lp_alignment_score
            },
            'strategy': miner_response.strategy.model_dump(),
            'miner_metadata': miner_response.miner_metadata.model_dump()
        }

        # Write to file for Executor Bot to read
        output_file = self.config.get('winning_strategy_file', 'winning_strategy.json')

        with open(output_file, 'w') as f:
            json.dump(output, f, indent=2)

        logger.info(f"Published winning strategy to {output_file}")

    def run_round(
        self,
        pair_address: str,
        target_block: int,
        inventory: Inventory,
        start_block: int
    ) -> Optional[MinerScore]:
        """
        Execute a complete validation round.

        Args:
            pair_address: Trading pair address
            target_block: Target block for predictions
            inventory: Available inventory
            start_block: Starting block for backtest

        Returns:
            MinerScore of the winning strategy, or None if no valid strategies
        """
        logger.info(f"Starting validation round for block {target_block}")

        # 1. Generate round request
        request = self.generate_round_request(
            pair_address=pair_address,
            target_block=target_block,
            inventory=inventory,
            mode=Mode.INVENTORY
        )

        # 2. Poll miners (or test miner if specified)
        test_miner_url = self.config.get('test_miner')
        if test_miner_url:
            logger.info(f"Using test miner at {test_miner_url}")
            response = self.query_test_miner(request, test_miner_url)
            if response:
                miner_responses = {-1: response}  # Use -1 as UID for test miner
            else:
                miner_responses = {}
        else:
            miner_responses = self.poll_miners(request)

        if not miner_responses:
            logger.warning("No valid miner responses received")
            return None

        # 3. Evaluate strategies
        scores = self.evaluate_strategies(
            miner_responses=miner_responses,
            request=request,
            start_block=start_block,
            end_block=target_block
        )

        if not scores:
            logger.warning("No strategies could be scored")
            return None

        # 4. Get winning strategy
        winning_score = self.scorer.get_winning_strategy(scores)

        if winning_score.miner_uid not in miner_responses:
            logger.error(f"Winning miner UID {winning_score.miner_uid} not found in responses")
            return None

        winning_response = miner_responses[winning_score.miner_uid]

        # 5. Publish scores and winning strategy
        self.publish_scores(scores)
        self.publish_winning_strategy(winning_score, winning_response)

        logger.info("Validation round completed")
        return winning_score

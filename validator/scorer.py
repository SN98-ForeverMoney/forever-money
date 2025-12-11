"""
Scoring system for SN98 - combines performance and LP alignment.
"""
import logging
from typing import List, Dict

from protocol import PerformanceMetrics
from validator.models import MinerScore

logger = logging.getLogger(__name__)


class Scorer:
    """
    Implements the 70/30 weighted scoring system:
    - 70% Net PnL vs HODL (top-heavy: only top 3 get full weight)
    - 30% LP Fee Share (pro-rata)
    """

    def __init__(
        self,
        performance_weight: float = 0.7,
        lp_alignment_weight: float = 0.3,
        top_n_strategies: int = 3
    ):
        self.performance_weight = performance_weight
        self.lp_alignment_weight = lp_alignment_weight
        self.top_n_strategies = top_n_strategies

    def calculate_performance_scores(
        self,
        miner_metrics: Dict[int, PerformanceMetrics]
    ) -> Dict[int, float]:
        """
        Calculate performance scores (70% component) with top-heavy weighting.

        Args:
            miner_metrics: Dictionary mapping miner_uid to PerformanceMetrics

        Returns:
            Dictionary mapping miner_uid to performance_score (0-1)
        """
        if not miner_metrics:
            return {}

        # Single miner case - give full score if positive PnL, 0.5 otherwise
        if len(miner_metrics) == 1:
            uid = list(miner_metrics.keys())[0]
            pnl = miner_metrics[uid].net_pnl_vs_hodl
            return {uid: 1.0 if pnl >= 0 else 0.5}

        # Extract Net PnL vs HODL for all miners
        pnl_scores = {
            uid: metrics.net_pnl_vs_hodl
            for uid, metrics in miner_metrics.items()
        }

        # Sort by PnL (descending)
        sorted_miners = sorted(
            pnl_scores.items(),
            key=lambda x: x[1],
            reverse=True
        )

        # Calculate normalized scores with top-heavy weighting
        performance_scores = {}

        # Top N strategies get full weight
        top_miners = sorted_miners[:self.top_n_strategies]
        remaining_miners = sorted_miners[self.top_n_strategies:]

        # Normalize top performers
        if top_miners:
            top_pnls = [pnl for _, pnl in top_miners]
            max_pnl = max(top_pnls)
            min_pnl = min(top_pnls)

            for rank, (uid, pnl) in enumerate(top_miners):
                # Handle edge cases for normalization
                if max_pnl == min_pnl:
                    # All top miners have same PnL - give equal scores
                    score = 1.0 if max_pnl >= 0 else 0.5
                elif max_pnl <= 0:
                    # All negative PnL - scale between 0.5 and 0.7
                    score = 0.5 + 0.2 * (pnl - min_pnl) / (max_pnl - min_pnl) if max_pnl != min_pnl else 0.5
                else:
                    # Normal case - scale between 0.5 and 1.0 for top N
                    # This ensures top miners are always differentiated
                    normalized = (pnl - min_pnl) / (max_pnl - min_pnl)
                    # Scale to 0.5-1.0 range to ensure top N always beat remaining
                    score = 0.5 + 0.5 * normalized

                performance_scores[uid] = score

        # Remaining miners get reduced scores
        if remaining_miners:
            for rank, (uid, pnl) in enumerate(remaining_miners, start=self.top_n_strategies):
                # Exponential decay for ranks beyond top N
                decay_factor = 0.5 ** ((rank - self.top_n_strategies) / 5)
                score = max(0.0, 0.4 * decay_factor)

                # If they have positive PnL vs HODL, give some credit
                if pnl > 0:
                    score = max(score, 0.1)

                performance_scores[uid] = score

        return performance_scores

    def calculate_lp_alignment_scores(
        self,
        vault_fees: Dict[int, float]
    ) -> Dict[int, float]:
        """
        Calculate LP alignment scores (30% component) pro-rata.

        Args:
            vault_fees: Dictionary mapping miner_uid to total fees collected

        Returns:
            Dictionary mapping miner_uid to lp_alignment_score (0-1)
        """
        if not vault_fees:
            return {}

        total_fees = sum(vault_fees.values())

        if total_fees == 0:
            # No fees collected by anyone - equal zero scores
            return {uid: 0.0 for uid in vault_fees.keys()}

        # Pro-rata scoring based on fee contribution
        lp_scores = {
            uid: fees / total_fees
            for uid, fees in vault_fees.items()
        }

        return lp_scores

    def calculate_final_scores(
        self,
        miner_metrics: Dict[int, PerformanceMetrics],
        vault_fees: Dict[int, float],
        miner_hotkeys: Dict[int, str],
        constraint_violations: Dict[int, List[str]]
    ) -> List[MinerScore]:
        """
        Calculate final weighted scores for all miners.

        Args:
            miner_metrics: Performance metrics for each miner
            vault_fees: LP fees collected by each miner
            miner_hotkeys: Mapping of UIDs to hotkeys
            constraint_violations: Constraint violations for each miner

        Returns:
            List of MinerScore objects, sorted by final_score descending
        """
        # Calculate component scores
        performance_scores = self.calculate_performance_scores(miner_metrics)
        lp_scores = self.calculate_lp_alignment_scores(vault_fees)

        # Get all miner UIDs
        all_uids = set(miner_metrics.keys()) | set(vault_fees.keys())

        # Build final scores
        final_scores = []

        for uid in all_uids:
            # Get component scores (default to 0 if missing)
            perf_score = performance_scores.get(uid, 0.0)
            lp_score = lp_scores.get(uid, 0.0)

            # Calculate weighted final score
            final_score = (
                perf_score * self.performance_weight +
                lp_score * self.lp_alignment_weight
            )

            # If there are constraint violations, score is 0
            violations = constraint_violations.get(uid, [])
            if violations:
                final_score = 0.0
                logger.warning(f"Miner {uid} has constraint violations, score set to 0")

            miner_score = MinerScore(
                miner_uid=uid,
                miner_hotkey=miner_hotkeys.get(uid, "unknown"),
                performance_score=perf_score,
                lp_alignment_score=lp_score,
                final_score=final_score,
                performance_metrics=miner_metrics.get(
                    uid,
                    PerformanceMetrics(
                        net_pnl=0.0,
                        hodl_pnl=0.0,
                        net_pnl_vs_hodl=0.0,
                        total_fees_collected=0.0,
                        impermanent_loss=0.0,
                        num_rebalances=0
                    )
                ),
                constraint_violations=violations
            )

            final_scores.append(miner_score)

        # Sort by final score (descending)
        final_scores.sort(key=lambda x: x.final_score, reverse=True)

        # Assign ranks
        for rank, score in enumerate(final_scores, start=1):
            score.rank = rank

        return final_scores

    def get_winning_strategy(
        self,
        scores: List[MinerScore]
    ) -> MinerScore:
        """
        Get the winning strategy (highest score).

        Args:
            scores: List of MinerScore objects

        Returns:
            MinerScore with the highest final_score
        """
        if not scores:
            raise ValueError("No scores provided")

        # Scores should already be sorted, but ensure it
        winning_score = max(scores, key=lambda x: x.final_score)

        logger.info(
            f"Winning strategy: Miner {winning_score.miner_uid} "
            f"(hotkey: {winning_score.miner_hotkey}) "
            f"with score {winning_score.final_score:.4f}"
        )

        return winning_score

"""
Strategy scoring for per-job competition.

Design:
- Primary signal: Net PnL vs HODL as **return** (final - initial) / initial.
  No scaling — score is raw return (e.g. 0.05 for +5%). EMA and ranking use
  relative values only; no other code depends on scale.
- Loss penalty: Use **impermanent_loss** from Backtester when present.
  Else fallback to token-delta loss. Zero loss ⇒ penalty = 1.
- Optional **in_range_ratio** bonus: reward time-in-range (more fee opportunity).
"""
import math
from typing import Dict, Any, List, Tuple

from validator.models.job import Job


DEFAULT_LOSS_PENALTY = 10.0
DEFAULT_IN_RANGE_WEIGHT = 0.08


def _get_loss_ratio(metrics: Dict[str, Any]) -> float:
    """
    Infer loss ratio for penalty. Prefer impermanent_loss; else token-delta.
    Returns 0 when there is no loss.
    """
    if "impermanent_loss" in metrics:
        il = metrics["impermanent_loss"]
        if il is not None:
            return float(il)
    initial = metrics.get("initial_inventory")
    final = metrics.get("final_inventory")
    if not initial or not final:
        return 0.0
    a0 = int(initial.amount0)
    a1 = int(initial.amount1)
    f0 = int(final.amount0)
    f1 = int(final.amount1)
    loss0 = max(0, a0 - f0) / a0 if a0 > 0 else 0.0
    loss1 = max(0, a1 - f1) / a1 if a1 > 0 else 0.0
    return max(loss0, loss1)


class Scorer:
    """
    Strategy scoring and winner ranking.

    - score_pol_strategy: strategy score from backtest metrics.
    - rank_miners_by_score_and_history: rank by round score, tie-break by history.
    """

    @staticmethod
    async def score_pol_strategy(
        metrics: Dict[str, Any],
        loss_penalty_multiplier: float = DEFAULT_LOSS_PENALTY,
        smooth_beta: float = 4.0,
    ) -> float:
        """
        Score strategy from backtest metrics (Net PnL vs HODL + loss penalty).

        - Uses **return** (relative) as primary signal, scaled by 1000.
        - Penalizes **impermanent loss** (or token-delta fallback). Zero loss ⇒ no penalty.
        - Optional **in_range_ratio** bonus when provided.

        smooth_beta is ignored (kept for API compatibility).
        """
        initial_value = metrics.get("initial_value")
        final_value = metrics.get("final_value")
        if initial_value is None or final_value is None:
            return float("-inf")
        initial_value = float(initial_value)
        final_value = float(final_value)
        if initial_value <= 0:
            return float("-inf")

        return_pct = (final_value - initial_value) / initial_value
        return_pct = max(-10.0, min(10.0, return_pct))

        loss_ratio = _get_loss_ratio(metrics)
        penalty = math.exp(-loss_penalty_multiplier * loss_ratio)

        if return_pct >= 0:
            score = return_pct * penalty
        else:
            score = return_pct / penalty if penalty > 0 else return_pct

        if DEFAULT_IN_RANGE_WEIGHT > 0 and "in_range_ratio" in metrics:
            r = metrics["in_range_ratio"]
            if r is not None:
                r = max(0.0, min(1.0, float(r)))
                score *= (1.0 - DEFAULT_IN_RANGE_WEIGHT) + DEFAULT_IN_RANGE_WEIGHT * r

        return float(score)

    @staticmethod
    def rank_miners_by_score_and_history(
        round_scores: Dict[int, float],
        historic_scores: Dict[int, float],
    ) -> List[Tuple[int, float]]:
        """
        Rank miners by round score; tie-break by historic combined_score.

        Returns list of (miner_uid, round_score) sorted best-first.
        """
        def key(item: Tuple[int, float]) -> Tuple[float, float]:
            uid, rs = item
            hist = historic_scores.get(uid, 0.0)
            return (-rs, -hist)

        return sorted(
            [(uid, rs) for uid, rs in round_scores.items()],
            key=key,
        )

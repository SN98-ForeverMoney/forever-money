"""
Strategy scoring for per-job competition.

Design:
- Primary signal: Net PnL vs HODL as **return** (final - initial) / initial.
  No scaling — score is raw return (e.g. 0.05 for +5%). EMA and ranking use
  relative values only; no other code depends on scale.
"""

from typing import Dict, Any

DEFAULT_LOSS_PENALTY = 10.0
DEFAULT_IN_RANGE_WEIGHT = 0.08

# Bounded score range: JSON/DB-safe, no -inf. Worst = SCORE_MIN, Best ≈ SCORE_MAX.
SCORE_MIN = -100.0
SCORE_MAX = 10.0


class MinimalScorer:
    """
    Strategy scoring and winner ranking.

    - score_pol_strategy: strategy score from backtest metrics.
    """

    @staticmethod
    async def score_pol_strategy(
        metrics: Dict[str, Any],
        loss_penalty_multiplier: float = DEFAULT_LOSS_PENALTY,
        smooth_beta: float = 4.0,
    ) -> float:
        """
        Score strategy from backtest metrics (Net PnL vs HODL).

        - Uses **return** (relative) as primary signal, scaled by 1000.
        - Optional **in_range_ratio** bonus when provided.

        smooth_beta is ignored (kept for API compatibility).
        """
        initial_value = metrics.get("initial_value")
        final_value = metrics.get("final_value")
        if initial_value is None or final_value is None:
            return SCORE_MIN

        initial_value = float(initial_value)
        final_value = float(final_value)
        if initial_value <= 0:
            return SCORE_MIN

        score = (final_value - initial_value) / initial_value
        score = max(-10.0, min(10.0, score))
        score = max(SCORE_MIN, min(SCORE_MAX, score))
        return score

"""
Winner selection for evaluation rounds.

One winner per job; tie-breaking by historic combined_score (eval + live).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from validator.services.scorer import Scorer

logger = logging.getLogger(__name__)


async def select_winner(
    job_repository,
    job_id: str,
    scores: Dict[int, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """
    Select one winner per job from round scores.
    Tie-breaking: historic combined_score (eval + live) descending.

    Args:
        job_repository: JobRepository instance
        job_id: Job identifier
        scores: Dict mapping miner_uid -> { "score": float, "hotkey": str, ... }

    Returns:
        Dict with miner_uid, hotkey, score, or None if no scores.
    """
    if not scores:
        return None

    round_scores = {uid: data["score"] for uid, data in scores.items()}
    historic = await job_repository.get_historic_combined_scores(
        job_id, list(scores.keys())
    )
    ranked = Scorer.rank_miners_by_score_and_history(round_scores, historic)
    if not ranked:
        return None

    winner_uid, round_score = ranked[0]
    winner_data = scores[winner_uid]
    return {
        "miner_uid": winner_uid,
        "hotkey": winner_data["hotkey"],
        "score": winner_data["score"],
    }

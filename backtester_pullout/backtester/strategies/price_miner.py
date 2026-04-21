"""Price-prediction-driven LP strategy: reposition AHEAD of the move.

Thesis: if we can predict where price will be at t+H, center the LP range at
the predicted FUTURE tick — not the current tick. When price arrives we're
already optimally positioned.

Strategy mechanics:
  - Every decision, we get `predicted_return = log(p_{t+H}/p_t)` from the oracle.
  - Translate to a predicted future tick: tick_future ≈ current_tick + Δreturn / ln(1.0001)
  - Build the target range centered on tick_future with width = vol-scaled as in vol_miner.
  - REBALANCE if the target range's center is more than `rebalance_distance_ticks`
    away from the current position's center.
  - Otherwise HOLD.

No exits. No pull-outs. Always in a position, just positioned ahead.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Deque

from backtester_pullout.backtester.strategies.base import (
    DecisionContext, Strategy, StrategyAction, register_strategy,
)


# ln(1.0001) = exact base of Uniswap V3 tick math
_LN_1_0001 = math.log(1.0001)


@register_strategy("price_miner")
@dataclass
class PriceMiner:
    # Range width (same mechanism as volatility_miner)
    width_factor: float = 3.0
    volatility_window: int = 10
    min_width_spacings: int = 25           # min width = tick_spacing * this
    # How far the target center must move before we act (ticks).
    rebalance_distance_ticks: int = 30
    # Dust check (carried from vol_miner for range placement)
    dust_threshold: int = 10_000
    # Gate decisions to match miner cadence on Base (900s = ~450 blocks)
    min_blocks_between_decisions: int = 450

    _recent_ticks: Deque[int] = field(init=False, repr=False, default=None)
    _last_decision_block: int = field(init=False, repr=False, default=-(10 ** 18))

    def __post_init__(self):
        self._recent_ticks = deque(maxlen=self.volatility_window)

    # ---- vol-based width (same as volatility_miner) -------------------------
    def _volatility(self) -> float:
        ticks = list(self._recent_ticks)
        if len(ticks) < self.volatility_window:
            return 0.0
        changes = []
        for i in range(1, len(ticks)):
            prev = ticks[i - 1]
            if prev == 0:
                continue
            changes.append((ticks[i] - prev) / prev)
        if not changes:
            return 0.0
        mean = sum(changes) / len(changes)
        var = sum((x - mean) ** 2 for x in changes) / len(changes)
        return math.sqrt(var)

    def _width(self, tick_spacing: int) -> int:
        vol = self._volatility()
        min_w = tick_spacing * self.min_width_spacings
        return max(int(vol * self.width_factor), min_w)

    def _pick_range(self, center_tick: int, tick_spacing: int) -> tuple[int, int]:
        width = self._width(tick_spacing)
        center = (center_tick // tick_spacing) * tick_spacing
        lower = ((center - width) // tick_spacing) * tick_spacing
        upper = ((center + width) // tick_spacing) * tick_spacing
        if upper <= lower:
            upper = lower + tick_spacing
        return lower, upper

    # ---- Decision -----------------------------------------------------------
    def decide(self, ctx: DecisionContext) -> StrategyAction:
        self._recent_ticks.append(int(ctx.current_tick))

        # Gate to ~900s cadence (same as vol_miner)
        if self._last_decision_block > -(10 ** 17):
            if (ctx.block - self._last_decision_block) < self.min_blocks_between_decisions:
                return StrategyAction.hold()

        mu = ctx.predicted_return
        # Without a prediction yet, stay with default behavior (center on now)
        if math.isnan(mu):
            target_center = ctx.current_tick
        else:
            # Predicted future tick
            target_center = int(ctx.current_tick + mu / _LN_1_0001)

        target_lo, target_hi = self._pick_range(target_center, ctx.tick_spacing)

        # Not in position yet → enter at target
        if not ctx.in_position:
            self._last_decision_block = ctx.block
            return StrategyAction.enter(target_lo, target_hi)

        # In position → rebalance if target center is far from current center
        assert ctx.position is not None
        pos_tl, pos_tu = ctx.position
        pos_center = (pos_tl + pos_tu) // 2
        target_center_snapped = (target_lo + target_hi) // 2
        if abs(target_center_snapped - pos_center) < self.rebalance_distance_ticks:
            return StrategyAction.hold()

        # Skip no-op REBALANCE (same range)
        if (target_lo, target_hi) == (pos_tl, pos_tu):
            return StrategyAction.hold()

        self._last_decision_block = ctx.block
        return StrategyAction.rebalance(target_lo, target_hi)

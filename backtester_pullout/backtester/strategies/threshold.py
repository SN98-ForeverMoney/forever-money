"""Binary threshold + hysteresis + graduated + time-gated + min-hold strategies.

All share the same core idea: compare predicted vol to threshold(s); decide
ENTER/EXIT. Compositional wrappers (MinHold, TimeGated) can wrap any of them.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from backtester_pullout.backtester.strategies.base import (
    ActionKind,
    DecisionContext,
    Strategy,
    StrategyAction,
    register_strategy,
)


def _enter(ctx: DecisionContext) -> StrategyAction:
    tl, tu = ctx.default_range_fn(ctx.current_tick)
    return StrategyAction.enter(tl, tu)


# -----------------------------------------------------------------------------
# Binary threshold
# -----------------------------------------------------------------------------
@register_strategy("binary")
@dataclass
class BinaryThreshold:
    threshold: float

    def decide(self, ctx: DecisionContext) -> StrategyAction:
        pv = ctx.predicted_vol
        if math.isnan(pv):
            return StrategyAction.hold()
        if pv > self.threshold and ctx.in_position:
            return StrategyAction.exit()
        if pv <= self.threshold and not ctx.in_position:
            return _enter(ctx)
        return StrategyAction.hold()


# -----------------------------------------------------------------------------
# Hysteresis — exit at high, re-enter at low
# -----------------------------------------------------------------------------
@register_strategy("hysteresis")
@dataclass
class Hysteresis:
    threshold_high: float
    threshold_low: float

    def __post_init__(self):
        if self.threshold_low >= self.threshold_high:
            raise ValueError("threshold_low must be < threshold_high")

    def decide(self, ctx: DecisionContext) -> StrategyAction:
        pv = ctx.predicted_vol
        if math.isnan(pv):
            return StrategyAction.hold()
        if ctx.in_position and pv > self.threshold_high:
            return StrategyAction.exit()
        if not ctx.in_position and pv < self.threshold_low:
            return _enter(ctx)
        return StrategyAction.hold()


# -----------------------------------------------------------------------------
# Graduated — discretized pull-out at exit_at_fraction.
# True fractional L-splitting would need a SET_FRACTION action — not in scope.
# -----------------------------------------------------------------------------
@register_strategy("graduated")
@dataclass
class Graduated:
    low: float
    high: float
    exit_at_fraction: float = 0.5

    def __post_init__(self):
        if self.low >= self.high:
            raise ValueError("low must be < high")
        if not 0.0 < self.exit_at_fraction <= 1.0:
            raise ValueError("exit_at_fraction in (0, 1]")

    def decide(self, ctx: DecisionContext) -> StrategyAction:
        pv = ctx.predicted_vol
        if math.isnan(pv):
            return StrategyAction.hold()
        f = max(0.0, min(1.0, (pv - self.low) / (self.high - self.low)))
        if f >= self.exit_at_fraction and ctx.in_position:
            return StrategyAction.exit()
        if f < self.exit_at_fraction and not ctx.in_position:
            return _enter(ctx)
        return StrategyAction.hold()


# -----------------------------------------------------------------------------
# Composition wrappers
# -----------------------------------------------------------------------------
@register_strategy("min_hold")
@dataclass
class MinHold:
    """Wraps an inner strategy; blocks any action within `min_holding_blocks`
    of the last action. Config uses an `inner` sub-dict specifying the wrapped
    strategy — built lazily to avoid circular imports.
    """
    inner: dict                           # {"type": ..., "params": {...}}
    min_holding_blocks: int

    _built: Strategy = field(init=False, default=None, repr=False)

    def _inner(self) -> Strategy:
        if self._built is None:
            from backtester_pullout.backtester.strategies.base import build_strategy
            self._built = build_strategy(self.inner["type"], self.inner.get("params", {}))
        return self._built

    def decide(self, ctx: DecisionContext) -> StrategyAction:
        action = self._inner().decide(ctx)
        if action.kind is ActionKind.HOLD:
            return action
        if ctx.blocks_since_last_action < self.min_holding_blocks:
            return StrategyAction.hold()
        return action


@register_strategy("time_gated")
@dataclass
class TimeGated:
    """Only invoke the inner strategy every `decision_every_blocks` blocks."""
    inner: dict
    decision_every_blocks: int

    _built: Strategy = field(init=False, default=None, repr=False)
    _last_decided_block: int = field(init=False, default=-(10 ** 18), repr=False)

    def _inner(self) -> Strategy:
        if self._built is None:
            from backtester_pullout.backtester.strategies.base import build_strategy
            self._built = build_strategy(self.inner["type"], self.inner.get("params", {}))
        return self._built

    def decide(self, ctx: DecisionContext) -> StrategyAction:
        if ctx.block - self._last_decided_block < self.decision_every_blocks:
            return StrategyAction.hold()
        self._last_decided_block = ctx.block
        return self._inner().decide(ctx)


# -----------------------------------------------------------------------------
# Convenience: hysteresis + min_hold (the common "pull-out" recipe from the spec)
# -----------------------------------------------------------------------------
@register_strategy("hysteresis_with_holding")
class HysteresisWithHolding(MinHold):
    def __init__(self, threshold_high: float, threshold_low: float, min_holding_blocks: int):
        super().__init__(
            inner={"type": "hysteresis", "params": {
                "threshold_high": threshold_high,
                "threshold_low": threshold_low,
            }},
            min_holding_blocks=min_holding_blocks,
        )

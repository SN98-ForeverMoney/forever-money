"""Port of miner/volatility_miner.py logic for the backtester.

Differences from the production miner that are intentional:
  - We use REBALANCE atomically (burn+mint) instead of separate burn and
    mint transactions; saves one tx_cost per rebalance.
  - We don't model on-chain wallet calls — the engine handles burn/mint math.

What we mirror:
  - Volatility = std dev of relative tick changes over a deque(maxlen=window).
  - Width = max(volatility * width_factor, tick_spacing * min_width_spacings).
  - Range placement:
      both tokens: [center - width, center + width] snapped to spacing
      token0 dust: [center + ts, center + ts + 2w] (above current tick)
      token1 dust: [center - ts - 2w, center - ts] (below current tick)
  - Rebalance trigger: current tick within `edge_buffer_pct` of position edge.

Strategy params (all optional with defaults from the production miner):
  width_factor: float = 3.0
  volatility_window: int = 10
  min_width_spacings: int = 25            # min_width = tick_spacing * 25
  edge_buffer_pct: float = 0.20           # 20% buffer triggers rebalance
  dust_threshold: int = 10_000            # below this is dust (raw token units)
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Optional

from backtester_pullout.backtester.strategies.base import (
    DecisionContext,
    Strategy,
    StrategyAction,
    register_strategy,
)


@register_strategy("volatility_miner")
@dataclass
class VolatilityMiner:
    width_factor: float = 3.0
    volatility_window: int = 10
    min_width_spacings: int = 25
    edge_buffer_pct: float = 0.20
    dust_threshold: int = 10_000
    # Production miner runs every ~450 blocks (vault_executor_interval=900s
    # at Base's 2s block time). Without gating, we'd rebalance on every swap.
    min_blocks_between_decisions: int = 450

    # Optional vol-based pull-out. When predicted_vol exceeds exit_vol_threshold
    # while in position, EXIT (hold raw tokens). While out, re-ENTER once
    # predicted_vol falls below reentry_vol_threshold. Hysteresis band avoids
    # thrashing. If either is None, feature is disabled.
    exit_vol_threshold: Optional[float] = None
    reentry_vol_threshold: Optional[float] = None

    # State (stateful per instance; sweep cells get fresh instances)
    _recent_ticks: Deque[int] = field(init=False, repr=False, default=None)
    _last_decision_block: int = field(init=False, repr=False, default=-(10 ** 18))

    def __post_init__(self):
        self._recent_ticks = deque(maxlen=self.volatility_window)
        # Validate hysteresis pair
        if (self.exit_vol_threshold is not None
                and self.reentry_vol_threshold is not None
                and self.reentry_vol_threshold >= self.exit_vol_threshold):
            raise ValueError(
                "reentry_vol_threshold must be < exit_vol_threshold"
            )

    # ---- Vol ----------------------------------------------------------------
    def _volatility(self) -> float:
        ticks = list(self._recent_ticks)
        if len(ticks) < self.volatility_window:
            return 0.0
        # std dev of relative tick changes (matches miner/volatility_miner.py:354-364)
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

    # ---- Range placement ----------------------------------------------------
    def _pick_range(self, current_tick: int, tick_spacing: int,
                    inv0: int, inv1: int) -> tuple[int, int]:
        vol = self._volatility()
        min_width = tick_spacing * self.min_width_spacings
        width = max(int(vol * self.width_factor), min_width)
        # Snap center to spacing
        center = (current_tick // tick_spacing) * tick_spacing

        # Uniswap V3: range ABOVE current price is minted with token0 only
        # (position accumulates token1 as price rises into range).
        # Range BELOW current price is minted with token1 only.
        # So dust-side logic is INVERTED vs. what the miner comments say:
        #   only token0 available → range above (token0 needed to mint)
        #   only token1 available → range below (token1 needed to mint)
        if inv0 <= self.dust_threshold and inv1 > self.dust_threshold:
            # Only token1 → range BELOW current tick (needs token1 only)
            upper = center - tick_spacing
            lower = ((upper - 2 * width) // tick_spacing) * tick_spacing
        elif inv1 <= self.dust_threshold and inv0 > self.dust_threshold:
            # Only token0 → range ABOVE current tick (needs token0 only)
            lower = center + tick_spacing
            upper = ((lower + 2 * width) // tick_spacing) * tick_spacing
        else:
            # Both → centered
            lower = ((center - width) // tick_spacing) * tick_spacing
            upper = ((center + width) // tick_spacing) * tick_spacing

        # Guard against degenerate ranges
        if upper <= lower:
            upper = lower + tick_spacing
        return lower, upper

    # ---- Decision -----------------------------------------------------------
    def decide(self, ctx: DecisionContext) -> StrategyAction:
        # Always track recent ticks (independent of decision cadence)
        self._recent_ticks.append(int(ctx.current_tick))

        # Gate ALL decisions after the first one.
        if self._last_decision_block > -(10 ** 17):
            if (ctx.block - self._last_decision_block) < self.min_blocks_between_decisions:
                return StrategyAction.hold()

        inv0, inv1 = ctx.inventory
        pv = ctx.predicted_vol

        # --- Vol-based pull-out logic --------------------------------------
        # (Only active if both thresholds configured; uses predicted vol from
        # the VolOracle, not our internal tick-deque vol.)
        if self.exit_vol_threshold is not None and self.reentry_vol_threshold is not None:
            import math
            if not math.isnan(pv):
                if ctx.in_position and pv > self.exit_vol_threshold:
                    self._last_decision_block = ctx.block
                    return StrategyAction.exit()
                if (not ctx.in_position) and pv < self.reentry_vol_threshold:
                    tl, tu = self._pick_range(ctx.current_tick, ctx.tick_spacing, inv0, inv1)
                    self._last_decision_block = ctx.block
                    return StrategyAction.enter(tl, tu)

        # --- First-time / out-of-position entry (no pull-out gating blocking) -
        if not ctx.in_position:
            # If vol thresholds are configured and vol still above reentry, hold.
            if (self.reentry_vol_threshold is not None and not math.isnan(pv)
                    and pv >= self.reentry_vol_threshold):
                return StrategyAction.hold()
            tl, tu = self._pick_range(ctx.current_tick, ctx.tick_spacing, inv0, inv1)
            self._last_decision_block = ctx.block
            return StrategyAction.enter(tl, tu)

        # --- Edge-buffer rebalance (in position) ---------------------------
        assert ctx.position is not None
        pos_tl, pos_tu = ctx.position
        tick_width = pos_tu - pos_tl
        if tick_width <= 0:
            tl, tu = self._pick_range(ctx.current_tick, ctx.tick_spacing, inv0, inv1)
            self._last_decision_block = ctx.block
            return StrategyAction.rebalance(tl, tu)

        buffer = tick_width * self.edge_buffer_pct
        near_lower = ctx.current_tick < pos_tl + buffer
        near_upper = ctx.current_tick > pos_tu - buffer
        if near_lower or near_upper:
            tl, tu = self._pick_range(ctx.current_tick, ctx.tick_spacing, inv0, inv1)
            if (tl, tu) == (pos_tl, pos_tu):
                return StrategyAction.hold()
            self._last_decision_block = ctx.block
            return StrategyAction.rebalance(tl, tu)

        return StrategyAction.hold()

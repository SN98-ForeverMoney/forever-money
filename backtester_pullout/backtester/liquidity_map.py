"""Reconstruct active liquidity per tick from mint + burn events.

Standard Uniswap V3 trick:
    For each position [tick_lower, tick_upper] of liquidity L:
        liquidity_net[tick_lower] += L
        liquidity_net[tick_upper] -= L
    Active L at tick T = sum of liquidity_net[t] for all t <= T

Fast lookups: keep liquidity_net as a sorted dict, plus a numpy-style
prefix-sum representation that we lazily rebuild when needed.

Mints add positive L; burns subtract. The data layer encodes this via the
`kind` column (+1 mint, -1 burn) on the unioned events stream.

Two main interfaces:
    1. apply(events_df) — bulk-apply a chronological batch of mint/burn events.
    2. active_L_at(tick) — query active liquidity at a tick (uses sorted scan).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

import numpy as np
import pandas as pd


@dataclass
class LiquidityMap:
    """Sparse tick → net liquidity delta map.

    Use `apply` to feed mint/burn events and `active_L_at` to query.

    Internal state:
        net: dict[tick, int]      — net delta at each tick (after all events applied)
        _sorted_ticks: np.ndarray — cached sorted tick array for fast bisect
        _cum: np.ndarray          — cumulative sum at each sorted tick (active L just past it)
        _dirty: bool              — set when net changes; sorted/cum rebuilt on next query
    """
    net: Dict[int, int] = field(default_factory=dict)
    _sorted_ticks: np.ndarray = field(default=None, init=False, repr=False)
    _cum: np.ndarray = field(default=None, init=False, repr=False)
    _dirty: bool = field(default=True, init=False, repr=False)

    # ---- mutation -----------------------------------------------------------
    def add_position(self, tick_lower: int, tick_upper: int, L: int) -> None:
        """Apply a mint (L > 0) or burn (L < 0). Net deltas only — does not validate."""
        if L == 0:
            return
        self.net[tick_lower] = self.net.get(tick_lower, 0) + L
        self.net[tick_upper] = self.net.get(tick_upper, 0) - L
        self._dirty = True

    def apply(self, events: pd.DataFrame) -> None:
        """Bulk-apply mint/burn events.

        events columns required: tick_lower, tick_upper, amount (uint), kind (+1/-1).
        """
        if events is None or len(events) == 0:
            return
        for tl, tu, amt, kind in zip(
            events["tick_lower"].to_numpy(),
            events["tick_upper"].to_numpy(),
            events["amount"].to_list(),  # python ints
            events["kind"].to_numpy(),
        ):
            L = int(amt) * int(kind)
            if L == 0:
                continue
            self.net[int(tl)] = self.net.get(int(tl), 0) + L
            self.net[int(tu)] = self.net.get(int(tu), 0) - L
        self._dirty = True

    # ---- queries ------------------------------------------------------------
    def _rebuild(self) -> None:
        if not self.net:
            self._sorted_ticks = np.array([], dtype=np.int64)
            self._cum = np.array([], dtype=object)
            self._dirty = False
            return
        ticks = sorted(self.net.keys())
        deltas = [self.net[t] for t in ticks]
        cum: List[int] = []
        running = 0
        for d in deltas:
            running += d
            cum.append(running)
        self._sorted_ticks = np.array(ticks, dtype=np.int64)
        self._cum = np.array(cum, dtype=object)  # python ints, no precision loss
        self._dirty = False

    def active_L_at(self, tick: int) -> int:
        """Active liquidity at the given tick.

        Active L at tick T = sum of liquidity_net[t] for t <= T.
        Equivalently: cumulative sum at the largest sorted tick <= T.
        """
        if self._dirty:
            self._rebuild()
        if len(self._sorted_ticks) == 0:
            return 0
        # idx = position where ticks[idx] > T → cumulative index = idx-1
        idx = int(np.searchsorted(self._sorted_ticks, tick, side="right")) - 1
        if idx < 0:
            return 0
        return int(self._cum[idx])

    def num_ticks_with_liquidity(self) -> int:
        """Diagnostic: how many distinct ticks have non-zero net deltas."""
        return sum(1 for v in self.net.values() if v != 0)

    def total_net(self) -> int:
        """Diagnostic: sum of all net deltas (should be 0 if all positions are still open)."""
        return sum(self.net.values())

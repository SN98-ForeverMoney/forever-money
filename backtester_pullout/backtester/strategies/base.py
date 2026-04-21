"""Strategy Protocol + registry.

The engine calls `Strategy.decide(ctx)` once per decision point and acts on
the returned StrategyAction.

StrategyAction kinds:
  HOLD       — no change
  ENTER(lo,hi) — mint at the given range from current idle (only when not in position)
  EXIT       — burn current position to raw tokens
  REBALANCE(lo,hi) — atomic burn current + mint at new range (only when in position)

For strategies that don't care about ranges (e.g. binary threshold), use the
ctx.default_range_fn(current_tick) helper which returns the config-default range.

A strategy may be stateful (hysteresis, min-hold, vol miner) — state lives on
the instance.

To add a strategy:
    @register_strategy("my_name")
    class MyStrategy(Strategy):
        def __init__(self, threshold: float):
            self.threshold = threshold
        def decide(self, ctx) -> StrategyAction: ...
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, Optional, Protocol, Tuple, Type, runtime_checkable


class ActionKind(Enum):
    HOLD = auto()
    ENTER = auto()
    EXIT = auto()
    REBALANCE = auto()


@dataclass(frozen=True)
class StrategyAction:
    """Action returned by a strategy.

    HOLD/EXIT carry no extra data. ENTER/REBALANCE require tick_lower/tick_upper.
    Use the classmethod constructors for clarity:
        StrategyAction.hold()
        StrategyAction.exit()
        StrategyAction.enter(tl, tu)
        StrategyAction.rebalance(tl, tu)
    """
    kind: ActionKind
    tick_lower: Optional[int] = None
    tick_upper: Optional[int] = None

    @classmethod
    def hold(cls) -> "StrategyAction":
        return cls(ActionKind.HOLD)

    @classmethod
    def exit(cls) -> "StrategyAction":
        return cls(ActionKind.EXIT)

    @classmethod
    def enter(cls, tick_lower: int, tick_upper: int) -> "StrategyAction":
        return cls(ActionKind.ENTER, tick_lower, tick_upper)

    @classmethod
    def rebalance(cls, tick_lower: int, tick_upper: int) -> "StrategyAction":
        return cls(ActionKind.REBALANCE, tick_lower, tick_upper)


@dataclass
class DecisionContext:
    """Inputs a strategy sees at each decision point.

    Add fields here when strategies need new signals. Strategies ignore fields
    they don't use.
    """
    # Time / price
    block: int
    sqrt_price_x96: int
    current_tick: int

    # Vol oracle
    predicted_vol: float        # noisy oracle output; NaN if not yet available
    realized_vol: float         # true forward realized vol; NaN if unknown

    # Our state
    in_position: bool
    blocks_since_last_action: int
    inventory: Tuple[int, int]              # (token0, token1) raw — idle + position-equivalent
    position: Optional[Tuple[int, int]]     # (tick_lower, tick_upper) of current pos, or None

    # Pool info + helpers
    tick_spacing: int
    default_range_fn: Optional[Callable[[int], Tuple[int, int]]] = None
    """Returns (tick_lower, tick_upper) for the config-default range centered on
    the given current_tick. Strategies that don't care about range placement
    can call this on ENTER / REBALANCE."""

    # Price oracle — forward log-return μ_R(t, t+H) = log(p_{t+H}/p_t).
    # Defaults at the end so existing ctx constructors keep working.
    predicted_return: float = float("nan")
    realized_return: float = float("nan")


@runtime_checkable
class Strategy(Protocol):
    def decide(self, ctx: DecisionContext) -> StrategyAction: ...




_REGISTRY: Dict[str, Type[Strategy]] = {}


def register_strategy(name: str) -> Callable[[Type[Strategy]], Type[Strategy]]:
    def _wrap(cls: Type[Strategy]) -> Type[Strategy]:
        if name in _REGISTRY and _REGISTRY[name] is not cls:
            raise ValueError(f"Strategy name '{name}' already registered")
        _REGISTRY[name] = cls
        return cls
    return _wrap


def build_strategy(type_name: str, params: Dict[str, Any] | None = None) -> Strategy:
    if type_name not in _REGISTRY:
        raise KeyError(
            f"Unknown strategy '{type_name}'. Registered: {sorted(_REGISTRY)}. "
            "Did you call register_builtins() or import your module?"
        )
    cls = _REGISTRY[type_name]
    return cls(**(params or {}))


def list_strategies() -> list[str]:
    return sorted(_REGISTRY)

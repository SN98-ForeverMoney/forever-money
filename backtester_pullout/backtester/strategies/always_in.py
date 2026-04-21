"""Trivial strategy — never exits. Used as the baseline in step 6 and as
the reference against Passive LP (they should produce identical equity curves)."""
from backtester_pullout.backtester.strategies.base import (
    DecisionContext,
    Strategy,
    StrategyAction,
    register_strategy,
)


@register_strategy("always_in")
class AlwaysIn(Strategy):
    def decide(self, ctx: DecisionContext) -> StrategyAction:
        if not ctx.in_position:
            tl, tu = ctx.default_range_fn(ctx.current_tick)
            return StrategyAction.enter(tl, tu)
        return StrategyAction.hold()

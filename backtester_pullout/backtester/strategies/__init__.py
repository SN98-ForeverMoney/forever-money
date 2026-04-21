"""Strategy plugins.

Adding a new strategy:
    1. Create a new file in this package (e.g. mystrategy.py)
    2. Subclass Strategy, implement .decide(ctx)
    3. Decorate with @register_strategy("my_type_name")
    4. Import the module in register_builtins() below (or set BACKTESTER_STRATEGY_MODULES env var)

Then refer to it in YAML:
    strategy:
      type: "my_type_name"
      params: {...}

Params are validated at build time via `cls.__init__` signature.
"""
from backtester_pullout.backtester.strategies.base import (
    Strategy,
    StrategyAction,
    DecisionContext,
    register_strategy,
    build_strategy,
    list_strategies,
)


def register_builtins() -> None:
    """Import built-in strategy modules to populate the registry.

    Call this once before build_strategy(). Safe to call multiple times.
    """
    # Imports trigger @register_strategy side effects.
    from backtester_pullout.backtester.strategies import always_in  # noqa: F401
    from backtester_pullout.backtester.strategies import threshold  # noqa: F401
    from backtester_pullout.backtester.strategies import volatility_miner  # noqa: F401
    from backtester_pullout.backtester.strategies import price_miner  # noqa: F401


__all__ = [
    "Strategy",
    "StrategyAction",
    "DecisionContext",
    "register_strategy",
    "build_strategy",
    "list_strategies",
    "register_builtins",
]

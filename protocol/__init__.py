"""
Package containing protocol related logic for SN98 ForeverMoney subnet.

This package defines the shared data models and Bittensor synapses used for
communication between validators and miners.

NOTE: Validator-specific models are in validator.models
      Miner-specific models are in miner.models
"""

# Export shared models only
from protocol.models import (
    Mode,
    Inventory,
    CurrentPosition,
    Position,
    RebalanceRule,
    Strategy,
    PerformanceMetrics,
)

# Export synapses
from protocol.synapses import (
    StrategyRequest,
    RebalanceQuery,
)

__all__ = [
    # Shared Models
    "Mode",
    "Inventory",
    "CurrentPosition",
    "Position",
    "RebalanceRule",
    "Strategy",
    "PerformanceMetrics",
    # Synapses
    "StrategyRequest",
    "RebalanceQuery",
]

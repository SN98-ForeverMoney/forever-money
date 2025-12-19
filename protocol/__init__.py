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

# Export synapses (optional import - requires bittensor)
try:
    from protocol.synapses import (
        StrategyRequest,
        RebalanceQuery,
        MinerMetadata,
    )

    _SYNAPSES_AVAILABLE = True
except ImportError:
    # Bittensor not available - synapses won't be available
    # but core models will still work for testing
    StrategyRequest = None
    RebalanceQuery = None
    MinerMetadata = None
    _SYNAPSES_AVAILABLE = False

__all__ = [
    # Shared Models
    "Mode",
    "Inventory",
    "CurrentPosition",
    "Position",
    "RebalanceRule",
    "Strategy",
    "PerformanceMetrics",
    # Synapses (may be None if bittensor not installed)
    "StrategyRequest",
    "RebalanceQuery",
    "MinerMetadata",
]

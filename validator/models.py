"""
Validator-specific data models for SN98 ForeverMoney.

These models are used exclusively by the validator for configuration,
scoring, and internal operations.
"""
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, model_validator

from protocol.models import Inventory, CurrentPosition, Position, Mode, PerformanceMetrics


class Constraints(BaseModel):
    """Validation constraints for strategies."""
    max_il: float = Field(0.10, description="Maximum impermanent loss allowed")
    min_tick_width: int = Field(60, description="Minimum tick width for positions")
    max_rebalances: int = Field(4, description="Maximum number of rebalances allowed")


class ValidatorMetadata(BaseModel):
    """Metadata for the round."""
    round_id: str = Field(..., description="Unique round identifier")
    constraints: Constraints = Field(default_factory=Constraints)


class ValidatorRequest(BaseModel):
    """Request sent from Validator to Miner."""
    pair_address: str = Field(..., description="Address of the trading pair")
    chain_id: int = Field(8453, description="Chain ID (Base = 8453)")
    target_block: int = Field(..., description="Target block number for strategy")
    mode: Mode = Field(Mode.INVENTORY, description="Operation mode")
    inventory: Optional[Inventory] = Field(None, description="Available inventory")
    current_positions: Optional[List[CurrentPosition]] = Field(
        default_factory=list, description="Existing positions"
    )
    metadata: ValidatorMetadata = Field(..., description="Round metadata and constraints")
    postgres_access: Optional[Dict[str, Any]] = Field(
        None, description="Database access credentials"
    )

    @model_validator(mode='after')
    def validate_mode_inventory(self) -> 'ValidatorRequest':
        """Ensure inventory is provided when mode is INVENTORY."""
        if self.mode == Mode.INVENTORY and self.inventory is None:
            raise ValueError("inventory must be provided when mode is INVENTORY")
        return self


class MinerScore(BaseModel):
    """Complete scoring for a miner."""
    miner_uid: int = Field(..., description="Miner UID")
    miner_hotkey: str = Field(..., description="Miner hotkey")
    performance_score: float = Field(..., description="Performance score (0-1)")
    lp_alignment_score: float = Field(..., description="LP alignment score (0-1)")
    final_score: float = Field(..., description="Final weighted score")
    performance_metrics: PerformanceMetrics = Field(..., description="Detailed metrics")
    constraint_violations: List[str] = Field(
        default_factory=list, description="List of constraint violations"
    )
    rank: Optional[int] = Field(None, description="Rank among all miners")


class RebalanceRequest(BaseModel):
    """
    Request sent from Validator to Miner during backtesting to ask
    whether the miner wants to rebalance at a specific block.

    This enables Option 2 architecture: validators call miners during
    backtest simulation to let miners make rebalance decisions based
    on their own logic (ML models, external data, etc.)
    """
    block_number: int = Field(..., description="Current block number in simulation")
    current_price: float = Field(..., description="Current price (token1/token0)")
    current_positions: List[Position] = Field(..., description="Current LP positions")
    pair_address: str = Field(..., description="Pool address")
    chain_id: int = Field(8453, description="Chain ID")
    round_id: str = Field(..., description="Round identifier for context")

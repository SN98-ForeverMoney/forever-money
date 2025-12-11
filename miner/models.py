"""
Miner-specific data models for SN98 ForeverMoney.

These models are used exclusively by miners for responses
and internal miner operations.
"""
from typing import List, Optional
from pydantic import BaseModel, Field, model_validator

from protocol.models import Strategy, Position
from protocol.synapses import MinerMetadata


class MinerResponse(BaseModel):
    """Response from Miner to Validator."""
    strategy: Strategy = Field(..., description="Proposed strategy")
    miner_metadata: MinerMetadata = Field(..., description="Miner metadata")


class RebalanceResponse(BaseModel):
    """
    Response from Miner indicating whether to rebalance and new positions.
    """
    rebalance: bool = Field(..., description="Whether to rebalance")
    new_positions: Optional[List[Position]] = Field(
        None, description="New positions if rebalancing (required if rebalance=True)"
    )
    reason: Optional[str] = Field(
        None, description="Optional explanation for the decision"
    )

    @model_validator(mode='after')
    def validate_positions_if_rebalance(self) -> 'RebalanceResponse':
        """Ensure new_positions is provided when rebalance is True."""
        if self.rebalance and (self.new_positions is None or len(self.new_positions) == 0):
            raise ValueError("new_positions must be provided when rebalance is True")
        return self

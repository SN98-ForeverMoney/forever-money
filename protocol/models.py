"""
Shared data models for SN98 ForeverMoney Validator-Miner communication.

This module contains ONLY shared models used for communication between
validators and miners. Validator-specific and miner-specific models
have been moved to their respective modules.
"""
from typing import List, Optional
from pydantic import BaseModel, Field, model_validator
from enum import Enum


class Mode(str, Enum):
    """Operation mode for strategy request."""
    INVENTORY = "inventory"
    POSITION = "position"


class Inventory(BaseModel):
    """Inventory of tokens available for deployment."""
    amount0: str = Field(..., description="Amount of token0 in wei")
    amount1: str = Field(..., description="Amount of token1 in wei")


class CurrentPosition(BaseModel):
    """Existing v3 LP position."""
    tick_lower: int = Field(..., description="Lower tick of position")
    tick_upper: int = Field(..., description="Upper tick of position")
    liquidity: str = Field(..., description="Liquidity amount")


class Position(BaseModel):
    """A single v3 LP position."""
    tick_lower: int = Field(..., description="Lower tick bound")
    tick_upper: int = Field(..., description="Upper tick bound")
    allocation0: str = Field(..., description="Amount of token0 to allocate")
    allocation1: str = Field(..., description="Amount of token1 to allocate")
    confidence: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="Confidence score (0-1)"
    )

    @model_validator(mode='after')
    def validate_tick_range(self) -> 'Position':
        """Ensure tick_upper > tick_lower."""
        if self.tick_upper <= self.tick_lower:
            raise ValueError("tick_upper must be greater than tick_lower")
        return self


class RebalanceRule(BaseModel):
    """Optional rebalance rule for the strategy."""
    trigger: str = Field(..., description="Trigger condition (e.g., 'price_outside_range')")
    cooldown_blocks: int = Field(..., description="Minimum blocks between rebalances")


class Strategy(BaseModel):
    """Complete strategy output from miner."""
    positions: List[Position] = Field(..., description="List of LP positions")
    rebalance_rule: Optional[RebalanceRule] = Field(
        None, description="Optional rebalance rule"
    )


class PerformanceMetrics(BaseModel):
    """Performance metrics for a strategy."""
    net_pnl: float = Field(..., description="Net PnL in base currency")
    hodl_pnl: float = Field(..., description="HODL baseline PnL")
    net_pnl_vs_hodl: float = Field(..., description="Net PnL vs HODL")
    total_fees_collected: float = Field(..., description="Total LP fees collected")
    impermanent_loss: float = Field(..., description="Impermanent loss incurred")
    num_rebalances: int = Field(..., description="Number of rebalances executed")

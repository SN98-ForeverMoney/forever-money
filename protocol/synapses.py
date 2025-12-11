"""
Bittensor Synapse definitions for SN98 ForeverMoney subnet.

Synapses define the request/response protocol between validators and miners
using Bittensor's dendrite/axon communication pattern.
"""
from typing import List, Optional, Dict, Any
import bittensor as bt
from pydantic import Field, BaseModel

from protocol.models import (
    Mode,
    Inventory,
    CurrentPosition,
    Position,
    Strategy,
)
from validator.models import ValidatorMetadata


class MinerMetadata(BaseModel):
    """Metadata about the miner's model."""
    version: str = Field(..., description="Miner version")
    model_info: str = Field(..., description="Model description")



class StrategyRequest(bt.Synapse):
    """
    Synapse for requesting LP strategy generation from miners.

    This replaces the HTTP POST /predict_strategy endpoint.

    Request fields (sent by validator):
        - pair_address: Trading pair address
        - chain_id: Blockchain ID
        - target_block: Block number for strategy
        - mode: INVENTORY or POSITION mode
        - inventory: Available tokens (if mode=INVENTORY)
        - current_positions: Existing positions (if mode=POSITION)
        - metadata: Round metadata and constraints
        - postgres_access: Optional DB credentials

    Response fields (returned by miner):
        - strategy: Generated strategy with positions
        - miner_metadata: Miner version and model info
    """

    # Request fields (inputs from validator)
    pair_address: str = Field(..., description="Address of the trading pair")
    chain_id: int = Field(8453, description="Chain ID (Base = 8453)")
    target_block: int = Field(..., description="Target block number for strategy")
    mode: Mode = Field(Mode.INVENTORY, description="Operation mode")
    inventory: Optional[Inventory] = Field(None, description="Available inventory")
    current_positions: Optional[List[CurrentPosition]] = Field(
        default_factory=list, description="Existing positions"
    )
    metadata: Optional[ValidatorMetadata] = Field(
        default=None, description="Round metadata and constraints"
    )
    postgres_access: Optional[Dict[str, Any]] = Field(
        None, description="Database access credentials"
    )

    # Response fields (outputs from miner)
    strategy: Optional[Strategy] = Field(None, description="Proposed strategy")
    miner_metadata: Optional[MinerMetadata] = Field(None, description="Miner metadata")

    def deserialize(self) -> "StrategyRequest":
        """
        Deserialize the synapse response.

        This method is called by the dendrite after receiving a response
        from the miner's axon.
        """
        return self


class RebalanceQuery(bt.Synapse):
    """
    Synapse for querying miners about rebalance decisions during backtesting.

    This replaces the HTTP POST /should_rebalance endpoint.

    Request fields (sent by validator during backtest):
        - block_number: Current simulation block
        - current_price: Current price (token1/token0)
        - current_positions: Active LP positions
        - pair_address: Pool address
        - chain_id: Blockchain ID
        - round_id: Round identifier

    Response fields (returned by miner):
        - rebalance: Whether to rebalance
        - new_positions: New positions if rebalancing
        - reason: Optional explanation
    """

    # Request fields (inputs from validator)
    block_number: int = Field(..., description="Current block number in simulation")
    current_price: float = Field(..., description="Current price (token1/token0)")
    current_positions: List[Position] = Field(..., description="Current LP positions")
    pair_address: str = Field(..., description="Pool address")
    chain_id: int = Field(8453, description="Chain ID")
    round_id: str = Field(..., description="Round identifier for context")

    # Response fields (outputs from miner)
    rebalance: Optional[bool] = Field(None, description="Whether to rebalance")
    new_positions: Optional[List[Position]] = Field(
        None, description="New positions if rebalancing"
    )
    reason: Optional[str] = Field(
        None, description="Optional explanation for the decision"
    )

    def deserialize(self) -> "RebalanceQuery":
        """
        Deserialize the synapse response.

        This method is called by the dendrite after receiving a response
        from the miner's axon.
        """
        return self

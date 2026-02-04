"""
Bittensor Synapse definitions for SN98 ForeverMoney subnet.

The subnet uses a rebalance-only approach:
1. Validator queries miners with RebalanceQuery, asking miners for their suggestion how to deploy the inventory.
2. Miners decide how to deploy the inventory.
"""
from typing import List, Optional
import bittensor as bt
from pydantic import Field, BaseModel

from protocol.models import Position


class MinerMetadata(BaseModel):
    """Metadata about the miner's model."""

    version: str = Field(..., description="Miner version")
    model_info: str = Field(..., description="Model description")


class RebalanceQuery(bt.Synapse):
    """
    Synapse for querying miners about rebalance decisions during backtesting.

    The validator:
    1. Generates initial positions for a job
    2. Simulates trading with those positions
    3. Periodically queries miners with current state
    4. Miners respond with new positions (or None to keep current positions)

    Request fields (sent by validator):
        - job_id: Job identifier
        - sn_liquidity_manager_address: Vault managing the liquidity
        - pair_address: Pool address
        - chain_id: EVM Chain ID
        - round_id: Round identifier
        - round_type: 'evaluation' or 'live'
        - block_number: Current simulation block
        - current_price: Current price (token1/token0)
        - current_positions: Active LP positions
        - inventory_remaining: Available tokens for new positions
        - rebalances_so_far: Number of rebalances executed so far

    Response fields (returned by miner):
        - accepted: Whether miner accepts this job
        - refusal_reason: Reason if refused (only if accepted=False)
        - new_positions: New positions to rebalance to (None = keep current positions)
        - miner_metadata: Miner version and model info
    """

    # Job context
    job_id: str = Field(..., description="Job identifier")
    sn_liquidity_manager_address: str = Field(
        ..., description="Vault managing the liquidity"
    )
    pair_address: str = Field(..., description="Pool address")
    chain_id: int = Field(8453, description="Chain ID (Base = 8453)")
    round_id: str = Field(..., description="Round identifier")
    round_type: str = Field(..., description="Round type: 'evaluation' or 'live'")

    # Simulation state
    block_number: int = Field(..., description="Current block number in simulation")
    current_price: float = Field(..., description="Current price (token1/token0)")
    current_positions: List[Position] = Field(..., description="Current LP positions")
    inventory_remaining: Optional[dict] = Field(
        None, description="Available tokens (amount0, amount1)"
    )
    rebalances_so_far: int = Field(
        0, description="Number of rebalances executed so far"
    )
    tick_spacing: int = Field(
        200, description="Pool tick spacing from get_tick_spacing (for tick alignment)"
    )

    # Response fields (outputs from miner)
    accepted: bool = Field(True, description="Whether miner accepts this job")
    refusal_reason: Optional[str] = Field(
        None, description="Reason for refusal if declined"
    )
    desired_positions: Optional[List[Position]] = Field(
        None,
        description="Positions desired to be live. "
        "Any existing positions not matching the "
        "desired positions (up to 2% difference tolerance) will be burned.",
    )

    miner_metadata: Optional[MinerMetadata] = Field(None, description="Miner metadata")

    def deserialize(self) -> "RebalanceQuery":
        """
        Deserialize the synapse response.

        This method is called by the dendrite after receiving a response
        from the miner's axon.
        """
        return self

"""Pydantic config schema for the pull-out backtester.

Loaded from YAML. Strict: unknown fields raise. Validation enforces:
  - horizon_blocks / vol_bucket_blocks >= 5 (vol std dev is meaningless below this)
  - end_block > start_block
  - positive sizes / non-negative noise / sane bps
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


MIN_VOL_SAMPLES = 5  # horizon/bucket ratio floor — see Q-A


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TickWidthRange(_Strict):
    type: Literal["tick_width"]
    width_ticks: int = Field(gt=0)


class AbsoluteRange(_Strict):
    type: Literal["absolute"]
    tick_lower: int
    tick_upper: int

    @model_validator(mode="after")
    def _ordered(self):
        if self.tick_lower >= self.tick_upper:
            raise ValueError("tick_lower must be < tick_upper")
        return self


class PoolConfig(_Strict):
    address: str = Field(min_length=42, max_length=42)  # 0x + 40 hex
    symbol: str
    # populated by prepare_pool_config.py; required at runtime
    token0: str = Field(min_length=42, max_length=42)
    token1: str = Field(min_length=42, max_length=42)
    decimals0: int = Field(ge=0, le=36)
    decimals1: int = Field(ge=0, le=36)
    fee_tier: int = Field(gt=0, description="Pool fee in pip-like units (e.g. 3000 = 0.3%)")
    tick_spacing: int = Field(gt=0)

    range: TickWidthRange | AbsoluteRange = Field(discriminator="type")
    # `position_size_usd` is interpreted as token1 human units × 10^decimals1.
    # This is correct only when token1 is a USD-pegged stablecoin (USDC/USDT/DAI).
    # For non-stable token1 (e.g. cbBTC, WETH), set `position_size_token1_raw`
    # explicitly to the raw token1 amount you want (overrides the USD field).
    position_size_usd: float = Field(gt=0)
    position_size_token1_raw: Optional[int] = Field(default=None, ge=0)
    # Same interpretation issue: `tx_cost_usd` is actually in token1 human units.
    # For non-stable token1, set `tx_cost_token1_raw` in raw units explicitly.
    tx_cost_token1_raw: Optional[int] = Field(default=None, ge=0)
    tx_cost_usd: float = Field(ge=0, default=0.01)
    # Extra slippage bps ON TOP of the V3-simulated pool fee + price impact.
    # Models non-idealities like MEV / multi-hop routing. Default 0 since
    # the dynamic V3 simulation already captures most of the real cost.
    slippage_bps: int = Field(ge=0, default=0)
    # Blocks between strategy deciding an action and the action executing
    # on-chain. Models the detect-off-chain → prepare-tx → broadcast latency.
    # Applied to all actions (ENTER / EXIT / REBALANCE).
    action_delay_blocks: int = Field(ge=0, default=15)


class BacktestConfig(_Strict):
    start_block: int = Field(gt=0)
    end_block: int = Field(gt=0)

    @model_validator(mode="after")
    def _range(self):
        if self.end_block <= self.start_block:
            raise ValueError("end_block must be > start_block")
        return self


class PredictionConfig(_Strict):
    horizon_blocks: int = Field(gt=0)
    noise_sigma: float = Field(ge=0)
    vol_bucket_blocks: int = Field(gt=0)
    # Return-prediction noise: μ̂ = μ + N(0, σ). σ is absolute std-dev on
    # the predicted log-return (e.g., 0.001 = 10 bps error). σ=0 → perfect foresight.
    return_noise_sigma: float = Field(ge=0, default=0.0)

    @model_validator(mode="after")
    def _enough_samples(self):
        ratio = self.horizon_blocks / self.vol_bucket_blocks
        if ratio < MIN_VOL_SAMPLES:
            raise ValueError(
                f"horizon_blocks/vol_bucket_blocks={ratio:.2f} < {MIN_VOL_SAMPLES}; "
                "realized-vol std dev is meaningless below this — increase horizon "
                "or shrink bucket"
            )
        return self


class StrategyConfig(_Strict):
    type: str
    params: Dict[str, Any] = Field(default_factory=dict)


class SweepConfig(_Strict):
    """Each key maps to a list of values to enumerate over.

    Keys may name fields in prediction.* or strategy.params.* using dotted form,
    or top-level shorthand (resolved by the sweep runner).
    """
    grid: Dict[str, List[Any]] = Field(default_factory=dict)


class Config(_Strict):
    pools: List[PoolConfig]
    backtest: BacktestConfig
    prediction: PredictionConfig
    strategy: StrategyConfig
    sweep: Optional[SweepConfig] = None
    seed: int = 42

    @model_validator(mode="after")
    def _one_pool_per_run(self):
        # Spec: one pool at a time
        if len(self.pools) == 0:
            raise ValueError("at least one pool required")
        return self


def load_config(path: str | Path) -> Config:
    path = Path(path)
    with path.open() as f:
        raw = yaml.safe_load(f)
    return Config.model_validate(raw)

"""Correctness gate (step 7, option C):

(A) Synthetic full-pool test — when our L equals pool L, share = 0.5, so we
    should earn exactly ~half of (sum of in-side amounts × fee_rate) for the
    in-range swaps.
(B) Cross-check vs validator/services/backtester.py — identical inputs, identical
    per-swap fee outputs. Guards against regressions from the reference.

Mints/burns/collects tables are empty in our indexer, so a real Collect-replay
is not possible.
"""
from typing import Any, Dict, List, Optional

import pandas as pd
import pytest

from backtester_pullout.backtester.position import LPPosition, fee_rate_from_tier
from backtester_pullout.backtester.config import (
    AbsoluteRange,
    PoolConfig,
    PredictionConfig,
)
from backtester_pullout.backtester.engine import run_backtest
from backtester_pullout.backtester.strategies import build_strategy, register_builtins

from validator.services.backtester import BacktesterService
from validator.repositories.pool import DataSource
from validator.utils.math import UniswapV3Math
from protocol.models import Inventory, Position


register_builtins()


def _pool() -> PoolConfig:
    return PoolConfig(
        address="0x" + "a" * 40, symbol="T/U",
        token0="0x" + "1" * 40, token1="0x" + "2" * 40,
        decimals0=18, decimals1=6,
        fee_tier=3000, tick_spacing=10,
        range=AbsoluteRange(type="absolute", tick_lower=-100, tick_upper=100),
        position_size_usd=10_000, tx_cost_usd=0.0, slippage_bps=0,
    )


def _pred() -> PredictionConfig:
    return PredictionConfig(horizon_blocks=25, noise_sigma=0.0, vol_bucket_blocks=5)


def _synth_swaps(n: int, pool_L: int, amount_in_token0: int, base_tick: int = 0) -> pd.DataFrame:
    """All swaps at the same in-range tick, same sides. Our standard test frame."""
    sqrt_p = UniswapV3Math.get_sqrt_ratio_at_tick(base_tick)
    return pd.DataFrame([{
        "block": 1_000_000 + i,
        "time": 1_700_000_000 + i,
        "tick": base_tick,
        "sqrt_price_x96": sqrt_p,
        "amount0": amount_in_token0,    # token0 in (positive)
        "amount1": -amount_in_token0 // 2,
        "liquidity": pool_L,
    } for i in range(n)])


# ---------------------------------------------------------------------------
# (A) Synthetic full-pool test
# ---------------------------------------------------------------------------
def test_synthetic_our_L_equals_pool_L_earns_half_fees():
    """If our_L == pool_L (from the swap event), dilution gives share = 0.5.
    Expected passive fees0 (in token0) = Σ amount0 × fee_rate × 0.5
    """
    n = 500
    amount_in = 10**18
    fee_rate = 0.003
    # Choose a pool_L small enough that our LP (built from 10k "USD" = 10**10 raw)
    # ends up close to pool_L. In practice we'll just force it by picking a
    # tiny pool_L and letting our L dominate.
    # Easier: use the reference formula directly — build a position with known
    # L_us, then pick pool_L = L_us.
    pool = _pool()

    swaps = _synth_swaps(n=1, pool_L=1, amount_in_token0=amount_in)  # dummy, will rewrite
    # build the position as the engine would at tick 0 with range [-100,100],
    # then grab its L
    sqrt_p = UniswapV3Math.get_sqrt_ratio_at_tick(0)
    amount1_raw = int(pool.position_size_usd * 10**pool.decimals1)
    half = amount1_raw // 2
    Q192 = UniswapV3Math.Q192
    a0 = (half * Q192) // (sqrt_p * sqrt_p)
    a1 = half
    pos = LPPosition.from_amounts(
        tick_lower=-100, tick_upper=100,
        sqrt_price_x96=sqrt_p, amount0=a0, amount1=a1,
    )
    our_L = pos.liquidity

    # Now feed swaps with pool_L = our_L → share should be 0.5
    swaps = _synth_swaps(n=n, pool_L=our_L, amount_in_token0=amount_in)
    # Fee rate override: set fee_tier = 3000 on the pool
    res = run_backtest(swaps, pool, _pred(), build_strategy("always_in"))
    got = res.final_passive.position.fees0

    # Expected: n × amount_in × fee_rate × 0.5, with int truncation per swap
    per_swap = int(amount_in * fee_rate * 0.5)
    expected = n * per_swap

    assert got == expected, f"fees0 mismatch: got {got}, expected {expected}"


def test_synthetic_out_of_range_earns_nothing():
    """Sanity — already tested in engine tests, but kept here as a gate."""
    pool = _pool()  # range [-100, 100]
    swaps = _synth_swaps(n=100, pool_L=10**20, amount_in_token0=10**18, base_tick=500)
    res = run_backtest(swaps, pool, _pred(), build_strategy("always_in"))
    assert res.final_passive.position.fees0 == 0
    assert res.final_passive.position.fees1 == 0


# ---------------------------------------------------------------------------
# (B) Cross-check vs validator/services/backtester.py
# ---------------------------------------------------------------------------
class _MockDataSource(DataSource):
    """In-memory DataSource serving a pre-built swap list in the format
    validator/services/backtester.py expects (dict with 'block_number',
    'sqrt_price_x96', 'amount0', 'amount1', 'liquidity', 'id')."""
    def __init__(self, swap_events: List[Dict[str, Any]], initial_sqrt: int, final_sqrt: int):
        self._events = swap_events
        self._initial_sqrt = initial_sqrt
        self._final_sqrt = final_sqrt

    async def get_swap_events(self, pair_address, start_block=None, end_block=None):
        return [e for e in self._events
                if (start_block is None or e["block_number"] >= start_block)
                and (end_block is None or e["block_number"] <= end_block)]

    async def get_sqrt_price_at_block(self, pair_address, block_number):
        # Return initial for small block nums, final otherwise — crude but works
        # for our fixed-price test.
        return self._initial_sqrt if block_number <= self._events[0]["block_number"] else self._final_sqrt

    async def get_mint_events(self, *a, **k): return []
    async def get_burn_events(self, *a, **k): return []
    async def get_collect_events(self, *a, **k): return []
    async def get_fee_growth(self, *a, **k): return {}
    async def get_tick_at_block(self, *a, **k): return 0


@pytest.mark.asyncio
async def test_crosscheck_validator_backtester_fees_match():
    """Same position, same swaps → identical fees across both engines.

    Uses our own LPPosition to compute what fees *our engine* would accrue
    on a given swap list (no need to run the full pipeline), then runs
    validator's BacktesterService with the equivalent Position object.
    """
    sqrt_p = UniswapV3Math.get_sqrt_ratio_at_tick(0)
    tick_lower, tick_upper = -100, 100

    # Position inputs
    a0_in, a1_in = 10**18, 10**18

    # Build our position and manually accrue fees
    pos_ours = LPPosition.from_amounts(tick_lower, tick_upper, sqrt_p, a0_in, a1_in)

    n = 250
    amount_in = 5 * 10**17
    pool_L = pos_ours.liquidity * 3  # dilution ≠ trivial

    swap_dicts = [{
        "id": i,
        "block_number": 1_000_000 + i,
        "sqrt_price_x96": str(sqrt_p),  # validator code casts via int()
        "amount0": str(amount_in),
        "amount1": str(-amount_in // 2),
        "liquidity": str(pool_L),
    } for i in range(n)]

    # Accrue with our code
    for ev in swap_dicts:
        pos_ours.accrue_fee_from_swap(
            sqrt_price_x96=int(ev["sqrt_price_x96"]),
            amount0=int(ev["amount0"]),
            amount1=int(ev["amount1"]),
            pool_liquidity=int(ev["liquidity"]),
            fee_rate=0.003,
        )

    # Run validator's BacktesterService
    ds = _MockDataSource(swap_dicts, initial_sqrt=sqrt_p, final_sqrt=sqrt_p)
    bt = BacktesterService(data_source=ds)

    position_obj = Position(
        tick_lower=tick_lower, tick_upper=tick_upper,
        allocation0=str(a0_in), allocation1=str(a1_in),
    )
    # Rebalance must strictly precede first swap block (validator code uses >).
    rebalance_history = [{
        "block": 999_999,
        "new_positions": [position_obj],
        "inventory": Inventory(amount0=str(a0_in), amount1=str(a1_in)),
    }]

    result = await bt.evaluate_positions_performance(
        pair_address="0x" + "a" * 40,
        rebalance_history=rebalance_history,
        start_block=1_000_000,
        end_block=1_000_000 + n - 1,
        initial_inventory=Inventory(amount0=str(a0_in), amount1=str(a1_in)),
        fee_rate=0.003,
    )

    # Compare. Validator accumulates into a float (total_fees0 = 0.0) which can
    # lose int precision above ~2^53; we accumulate as Python int. So compare
    # within 1 ULP relative tolerance.
    def _close(a, b, rtol=1e-12):
        return abs(float(a) - float(b)) <= rtol * max(1.0, abs(float(b)))

    assert _close(result["fees0"], pos_ours.fees0), (
        f"fees0 mismatch: validator={result['fees0']}, ours={pos_ours.fees0}"
    )
    assert _close(result["fees1"], pos_ours.fees1), (
        f"fees1 mismatch: validator={result['fees1']}, ours={pos_ours.fees1}"
    )

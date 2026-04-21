"""Single-pass event-driven backtest engine.

Iterates swap events once and produces three equity curves in parallel:
  1. HODL 50/50           — fixed (a0, a1) from t=0, mark-to-market per swap
  2. Passive LP           — mint once at t=0, accrue fees, never exit
  3. Strategy             — follows a Strategy plugin's decisions (ENTER/EXIT/HOLD)

All P&L is in **token1 raw units** (matches validator/services/backtester.py).
Convert to token1-human at report time via 10**decimals1.

Position sizing:
  Config gives `position_size_usd`. When token1 is a stablecoin (USDC, USDT)
  this is interpreted directly as token1 human-units → scaled to raw by 10**decimals1.
  For non-stable token1 pools, user must convert externally (documented).

Strategy exit mode: hold raw tokens (no swap on exit). Re-entry mints LP
from available idle tokens.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional  # noqa: F401

import pandas as pd

from backtester_pullout.backtester.config import (
    AbsoluteRange,
    PoolConfig,
    PredictionConfig,
    TickWidthRange,
)
from backtester_pullout.backtester.liquidity_map import LiquidityMap
from backtester_pullout.backtester.position import (
    LPPosition,
    fee_rate_from_tier,
    swap_to_ratio,
    value_in_token1,
)
from backtester_pullout.backtester.strategies.base import (
    ActionKind,
    DecisionContext,
    Strategy,
    StrategyAction,
)
from backtester_pullout.backtester.vol import VolOracle, make_rng
from validator.utils.math import UniswapV3Math

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def resolve_range(pool: PoolConfig, entry_tick: int) -> tuple[int, int]:
    """Produce (tick_lower, tick_upper) from pool config given entry price tick."""
    if isinstance(pool.range, TickWidthRange):
        half = pool.range.width_ticks // 2
        # snap to tick_spacing
        ts = pool.tick_spacing
        lower = ((entry_tick - half) // ts) * ts
        upper = lower + (pool.range.width_ticks // ts) * ts
        if upper <= lower:
            upper = lower + ts
        return lower, upper
    elif isinstance(pool.range, AbsoluteRange):
        return pool.range.tick_lower, pool.range.tick_upper
    raise TypeError(f"Unknown range type: {type(pool.range)}")


def initial_amounts_5050(
    position_size_token1_raw: int,
    sqrt_price_x96: int,
) -> tuple[int, int]:
    """Split capital 50/50 by *value* at current price.

    Half the value goes to token1 directly. The other half is notionally
    "worth `half` in token1" and we convert to token0:
        amount0 = half * Q192 / (sqrt_price)^2   (inverse of Q192 valuation)
    """
    half = position_size_token1_raw // 2
    price_x192 = sqrt_price_x96 * sqrt_price_x96
    if price_x192 == 0:
        raise ValueError("sqrt_price_x96 is zero")
    amount0 = (half * UniswapV3Math.Q192) // price_x192
    amount1 = position_size_token1_raw - half  # capture the odd cent
    return amount0, amount1


# ----------------------------------------------------------------------------
# Leg state
# ----------------------------------------------------------------------------

@dataclass
class HodlLeg:
    """Fixed token amounts from t=0; only its mark-to-market changes."""
    amount0: int
    amount1: int

    def value(self, sqrt_price_x96: int) -> int:
        return value_in_token1(self.amount0, self.amount1, sqrt_price_x96)


@dataclass
class LpLeg:
    """LP position + idle tokens that didn't fit into L.

    When in-position: `position` holds L; `idle0/idle1` are the excess tokens
    from the mint (Uniswap rounds L down, leaves residue).
    When out-of-position: `position is None`; `idle0/idle1` hold the raw tokens
    returned by the burn.
    """
    position: Optional[LPPosition] = None
    idle0: int = 0
    idle1: int = 0
    # cumulative costs paid (token1 raw units); bookkeeping for later steps
    costs_paid_token1: int = 0
    num_rebalances: int = 0
    in_range_swap_count: int = 0
    observed_swap_count: int = 0

    def value(self, sqrt_price_x96: int) -> int:
        a0, a1 = self.idle0, self.idle1
        f0 = f1 = 0
        if self.position is not None:
            pa0, pa1 = self.position.amounts_at(sqrt_price_x96)
            a0 += pa0
            a1 += pa1
            f0 = self.position.fees0
            f1 = self.position.fees1
        return value_in_token1(a0 + f0, a1 + f1, sqrt_price_x96)

    def mint(
        self,
        tick_lower: int,
        tick_upper: int,
        sqrt_price_x96: int,
    ) -> None:
        """Mint as much of idle0/idle1 as possible into an LP position at given range."""
        if self.position is not None:
            raise RuntimeError("already in position — must burn before re-mint")

        L, used0, used1 = UniswapV3Math.position_liquidity_and_used_amounts(
            tick_lower, tick_upper, sqrt_price_x96, self.idle0, self.idle1,
        )
        if L <= 0:
            # Nothing to mint (idle amounts don't fit a positive L in range).
            return

        self.position = LPPosition(
            tick_lower=tick_lower,
            tick_upper=tick_upper,
            liquidity=L,
            sqrt_price_lower_x96=UniswapV3Math.get_sqrt_ratio_at_tick(tick_lower),
            sqrt_price_upper_x96=UniswapV3Math.get_sqrt_ratio_at_tick(tick_upper),
        )
        self.idle0 -= used0
        self.idle1 -= used1

    def burn(self, sqrt_price_x96: int) -> None:
        """Burn the LP position: convert L back to tokens at current price,
        add accrued fees to idle, return to "holding raw tokens" state."""
        if self.position is None:
            return
        a0, a1 = self.position.amounts_at(sqrt_price_x96)
        self.idle0 += a0 + self.position.fees0
        self.idle1 += a1 + self.position.fees1
        self.position = None


# ----------------------------------------------------------------------------
# Engine
# ----------------------------------------------------------------------------

@dataclass
class EngineResult:
    equity: pd.DataFrame            # cols: block, time, hodl, passive, strategy (token1 raw int), price (float token1/token0 human)
    actions: List[dict] = field(default_factory=list)
    final_passive: LpLeg = field(default_factory=LpLeg)
    final_strategy: LpLeg = field(default_factory=LpLeg)
    final_hodl: HodlLeg = field(default_factory=lambda: HodlLeg(0, 0))
    vol_oracle: Optional["VolOracle"] = None


def run_backtest(
    swaps: pd.DataFrame,
    pool: PoolConfig,
    prediction: PredictionConfig,
    strategy: Strategy,
    *,
    log_every: int = 0,
    seed: int = 42,
    cell_index: int = 0,
    vol_oracle: Optional[VolOracle] = None,
    liq_events_in_window: Optional[pd.DataFrame] = None,
    liq_map: Optional[LiquidityMap] = None,
) -> EngineResult:
    """Run the engine over a prepared swap frame.

    `swaps` must be sorted ascending by block and contain columns:
      block, time, tick, sqrt_price_x96 (int), amount0 (int), amount1 (int), liquidity (int).

    Optional liquidity reconstruction:
      * `liq_map` — pre-seeded LiquidityMap up to (start_block - 1). If provided
        we use `liq_map.active_L_at(current_tick)` for fee dilution instead of
        the raw `swap.liquidity` field. The caller is responsible for seeding
        from the pool's full history (use data.load_liq_events_upto).
      * `liq_events_in_window` — mint/burn events between start_block and
        end_block (use data.load_liq_events). Applied incrementally as we
        process swaps so the map stays current.
    """
    if len(swaps) == 0:
        raise ValueError("empty swap frame — nothing to backtest")

    # Build vol oracle once (or accept pre-built one from the sweep runner)
    if vol_oracle is None:
        vol_oracle = VolOracle.build(
            swaps,
            bucket_blocks=prediction.vol_bucket_blocks,
            horizon_blocks=prediction.horizon_blocks,
            noise_sigma=prediction.noise_sigma,
            return_noise_sigma=prediction.return_noise_sigma,
            rng=make_rng(seed, cell_index),
        )

    fee_rate = fee_rate_from_tier(pool.fee_tier)
    if pool.position_size_token1_raw is not None:
        position_size_raw = int(pool.position_size_token1_raw)
    else:
        # Assumes token1 is USD-pegged; correct for USDC/USDT/DAI pools
        position_size_raw = int(pool.position_size_usd * 10 ** pool.decimals1)

    # --- Initial state from first swap's sqrt_price ----------------------------
    first = swaps.iloc[0]
    sqrt_p0 = int(first["sqrt_price_x96"])
    tick0 = int(first["tick"])
    a0_init, a1_init = initial_amounts_5050(position_size_raw, sqrt_p0)

    tick_lower, tick_upper = resolve_range(pool, tick0)
    logger.info(
        f"Entry tick={tick0}, range=[{tick_lower}, {tick_upper}], "
        f"initial amounts (token0={a0_init}, token1={a1_init})"
    )

    # --- Three legs ------------------------------------------------------------
    hodl = HodlLeg(amount0=a0_init, amount1=a1_init)

    passive = LpLeg(idle0=a0_init, idle1=a1_init)
    passive.mint(tick_lower, tick_upper, sqrt_p0)

    strat_leg = LpLeg(idle0=a0_init, idle1=a1_init)
    strat_leg.mint(tick_lower, tick_upper, sqrt_p0)

    # tx_cost charged per on-chain action. Slippage applies to any swap-to-ratio
    # performed as part of a mint. EXIT (hold raw tokens) does not swap → no
    # slippage, only tx_cost.
    if pool.tx_cost_token1_raw is not None:
        tx_cost_raw = int(pool.tx_cost_token1_raw)
    else:
        tx_cost_raw = int(pool.tx_cost_usd * 10 ** pool.decimals1)
    passive.idle1 -= tx_cost_raw
    passive.costs_paid_token1 += tx_cost_raw
    strat_leg.idle1 -= tx_cost_raw
    strat_leg.costs_paid_token1 += tx_cost_raw

    actions: List[dict] = [{
        "block": int(first["block"]),
        "action": "ENTER",
        "leg": "strategy",
        "reason": "initial",
        "tick_lower": tick_lower,
        "tick_upper": tick_upper,
        "cost_token1": tx_cost_raw,
    }]

    # --- Equity curve storage ---------------------------------------------------
    # Pre-allocate numpy arrays — millions of swaps, can't afford list-of-dicts.
    n = len(swaps)
    blocks = swaps["block"].to_numpy()
    times = swaps["time"].to_numpy()
    hodl_eq = [0] * n
    passive_eq = [0] * n
    strat_eq = [0] * n

    # Iterate swaps once. Python ints preserve precision for Q192 math.
    sqrt_arr = swaps["sqrt_price_x96"].to_list()
    a0_arr = swaps["amount0"].to_list()
    a1_arr = swaps["amount1"].to_list()
    liq_arr = swaps["liquidity"].to_list()
    tick_arr = swaps["tick"].to_list()

    # Liquidity reconstruction setup
    using_liq_map = liq_map is not None
    if using_liq_map and liq_events_in_window is not None and len(liq_events_in_window) > 0:
        # Pointer into liq_events_in_window (sorted by block ascending)
        liq_events_blocks = liq_events_in_window["block"].to_numpy()
        liq_events_tl = liq_events_in_window["tick_lower"].to_numpy()
        liq_events_tu = liq_events_in_window["tick_upper"].to_numpy()
        liq_events_amt = liq_events_in_window["amount"].to_list()
        liq_events_kind = liq_events_in_window["kind"].to_numpy()
    else:
        liq_events_blocks = liq_events_tl = liq_events_tu = liq_events_kind = []
        liq_events_amt = []
    liq_events_idx = 0
    liq_events_n = len(liq_events_blocks) if hasattr(liq_events_blocks, '__len__') else 0

    # Diagnostic counters
    liq_diff_count = 0
    liq_total_count = 0
    liq_relerr_sum = 0.0

    last_action_block = int(first["block"])

    # Pending-action queue. When the strategy decides an action at block B,
    # it's recorded here and applied only at block >= B + action_delay_blocks.
    # This models real-world off-chain detect → prepare-tx → broadcast latency.
    pending: Optional[dict] = None  # {"action": StrategyAction, "execute_at": int, "decided_at": int, "ctx": dict}

    for i in range(n):
        sqrt_p = int(sqrt_arr[i])
        a0 = int(a0_arr[i])
        a1 = int(a1_arr[i])
        pool_L_event = int(liq_arr[i])
        block = int(blocks[i])
        cur_tick = int(tick_arr[i])

        # Drain mint/burn events that occurred at or before this swap block
        if using_liq_map:
            while (liq_events_idx < liq_events_n
                   and int(liq_events_blocks[liq_events_idx]) <= block):
                tl = int(liq_events_tl[liq_events_idx])
                tu = int(liq_events_tu[liq_events_idx])
                amt = int(liq_events_amt[liq_events_idx]) * int(liq_events_kind[liq_events_idx])
                liq_map.add_position(tl, tu, amt)
                liq_events_idx += 1

            pool_L = liq_map.active_L_at(cur_tick)
            liq_total_count += 1
            if pool_L != pool_L_event:
                liq_diff_count += 1
                if pool_L_event > 0:
                    liq_relerr_sum += abs(pool_L - pool_L_event) / pool_L_event
        else:
            pool_L = pool_L_event

        # 1. Accrue fees on all active LP legs
        for leg in (passive, strat_leg):
            leg.observed_swap_count += 1
            if leg.position is not None and leg.position.in_range(sqrt_p):
                leg.in_range_swap_count += 1
                leg.position.accrue_fee_from_swap(
                    sqrt_p, a0, a1, pool_L, fee_rate,
                )

        # 2a. Execute any pending action that's due at this block (using
        # the current/executing-time sqrt_p, not the decision-time price).
        if pending is not None and block >= pending["execute_at"]:
            p_action = pending["action"]
            decided_at = pending["decided_at"]
            pv_at_decision = pending["predicted_vol_at_decision"]
            rv_at_decision = pending["realized_vol_at_decision"]

            if p_action.kind is ActionKind.EXIT and strat_leg.position is not None:
                strat_leg.burn(sqrt_p)
                strat_leg.idle1 -= tx_cost_raw
                strat_leg.costs_paid_token1 += tx_cost_raw
                strat_leg.num_rebalances += 1
                actions.append({
                    "block": block, "action": "EXIT", "leg": "strategy",
                    "reason": "strategy_decide", "decided_at": decided_at,
                    "cost_token1": tx_cost_raw, "swap_amount": 0, "swap_side": -1,
                    "predicted_vol": pv_at_decision, "realized_vol": rv_at_decision,
                })
                last_action_block = block

            elif p_action.kind is ActionKind.ENTER and strat_leg.position is None:
                tl = p_action.tick_lower if p_action.tick_lower is not None else None
                tu = p_action.tick_upper if p_action.tick_upper is not None else None
                if tl is None or tu is None:
                    tl, tu = resolve_range(pool, cur_tick)
                # Swap-to-ratio using current idle at execution price
                new_a0, new_a1, sw_amt, sw_side = swap_to_ratio(
                    strat_leg.idle0, strat_leg.idle1, sqrt_p, tl, tu,
                    active_L=pool_L, pool_fee_tier=pool.fee_tier,
                    extra_slippage_bps=pool.slippage_bps,
                )
                strat_leg.idle0, strat_leg.idle1 = new_a0, new_a1
                strat_leg.mint(tl, tu, sqrt_p)
                strat_leg.idle1 -= tx_cost_raw
                strat_leg.costs_paid_token1 += tx_cost_raw
                strat_leg.num_rebalances += 1
                actions.append({
                    "block": block, "action": "ENTER", "leg": "strategy",
                    "reason": "strategy_decide", "decided_at": decided_at,
                    "cost_token1": tx_cost_raw, "swap_amount": sw_amt, "swap_side": sw_side,
                    "predicted_vol": pv_at_decision, "realized_vol": rv_at_decision,
                    "tick_lower": tl, "tick_upper": tu,
                })
                last_action_block = block

            elif p_action.kind is ActionKind.REBALANCE and strat_leg.position is not None:
                tl = p_action.tick_lower
                tu = p_action.tick_upper
                if tl is None or tu is None:
                    actions.append({"block": block, "action": "REBALANCE_SKIPPED",
                                    "leg": "strategy", "reason": "missing_range",
                                    "decided_at": decided_at, "cost_token1": 0})
                else:
                    # Post-burn idle, then swap-to-ratio, then mint
                    pa0, pa1 = strat_leg.position.amounts_at(sqrt_p)
                    post_burn_idle0 = strat_leg.idle0 + pa0 + strat_leg.position.fees0
                    post_burn_idle1 = strat_leg.idle1 + pa1 + strat_leg.position.fees1
                    new_a0, new_a1, sw_amt, sw_side = swap_to_ratio(
                        post_burn_idle0, post_burn_idle1, sqrt_p, tl, tu,
                        active_L=pool_L, pool_fee_tier=pool.fee_tier,
                        extra_slippage_bps=pool.slippage_bps,
                    )
                    # Verify mint will succeed at the balanced ratio
                    L_check, _, _ = UniswapV3Math.position_liquidity_and_used_amounts(
                        tl, tu, sqrt_p, new_a0, new_a1,
                    )
                    if L_check <= 0:
                        actions.append({
                            "block": block, "action": "REBALANCE_SKIPPED", "leg": "strategy",
                            "reason": "mint_would_yield_zero_L_after_swap",
                            "decided_at": decided_at, "cost_token1": 0,
                            "tick_lower": tl, "tick_upper": tu,
                        })
                    else:
                        strat_leg.burn(sqrt_p)
                        strat_leg.idle0, strat_leg.idle1 = new_a0, new_a1
                        strat_leg.mint(tl, tu, sqrt_p)
                        strat_leg.idle1 -= tx_cost_raw
                        strat_leg.costs_paid_token1 += tx_cost_raw
                        strat_leg.num_rebalances += 1
                        actions.append({
                            "block": block, "action": "REBALANCE", "leg": "strategy",
                            "reason": "strategy_decide", "decided_at": decided_at,
                            "cost_token1": tx_cost_raw, "swap_amount": sw_amt,
                            "swap_side": sw_side,
                            "predicted_vol": pv_at_decision, "realized_vol": rv_at_decision,
                            "tick_lower": tl, "tick_upper": tu,
                        })
                        last_action_block = block

            pending = None

        # 2b. Build decision context and ask the strategy — but only if
        # no pending action is in flight.
        inv0, inv1 = strat_leg.idle0, strat_leg.idle1
        pos_range: Optional[tuple] = None
        if strat_leg.position is not None:
            pa0, pa1 = strat_leg.position.amounts_at(sqrt_p)
            inv0 += pa0 + strat_leg.position.fees0
            inv1 += pa1 + strat_leg.position.fees1
            pos_range = (strat_leg.position.tick_lower, strat_leg.position.tick_upper)

        ctx = DecisionContext(
            block=block,
            sqrt_price_x96=sqrt_p,
            current_tick=cur_tick,
            predicted_vol=vol_oracle.predicted_vol(block),
            realized_vol=vol_oracle.realized_vol(block),
            predicted_return=vol_oracle.predicted_return_at(block),
            realized_return=vol_oracle.realized_return_at(block),
            in_position=strat_leg.position is not None,
            blocks_since_last_action=block - last_action_block,
            inventory=(inv0, inv1),
            position=pos_range,
            tick_spacing=pool.tick_spacing,
            default_range_fn=lambda t, p=pool: resolve_range(p, t),
        )

        if pending is None:
            action = strategy.decide(ctx)
            if action.kind is not ActionKind.HOLD:
                pending = {
                    "action": action,
                    "decided_at": block,
                    "execute_at": block + pool.action_delay_blocks,
                    "predicted_vol_at_decision": ctx.predicted_vol,
                    "realized_vol_at_decision": ctx.realized_vol,
                }

        # 3. Mark-to-market all three
        hodl_eq[i] = hodl.value(sqrt_p)
        passive_eq[i] = passive.value(sqrt_p)
        strat_eq[i] = strat_leg.value(sqrt_p)

        if log_every and (i + 1) % log_every == 0:
            extra = ""
            if using_liq_map:
                pct = 100.0 * liq_diff_count / max(1, liq_total_count)
                avg_relerr = liq_relerr_sum / max(1, liq_total_count) * 100.0
                extra = f" (L map ≠ swap.liquidity at {pct:.2f}% of swaps; avg rel-err {avg_relerr:.4f}%)"
            logger.info(f"  processed {i+1}/{n} swaps{extra}")

    # Human-readable price token1/token0 at each swap:
    #   raw = (sqrt_price_x96 / 2^96)^2        (token1/token0 in wei ratio)
    #   price = raw * 10^(decimals0 - decimals1)   (human units)
    Q96 = UniswapV3Math.Q96
    dec_scale = 10 ** (pool.decimals0 - pool.decimals1)
    prices = [float(int(s)) / Q96 for s in sqrt_arr]
    prices = [(p * p) * dec_scale for p in prices]

    equity = pd.DataFrame({
        "block": blocks,
        "time": times,
        "hodl": hodl_eq,
        "passive": passive_eq,
        "strategy": strat_eq,
        "price": prices,
    })

    return EngineResult(
        equity=equity,
        actions=actions,
        final_passive=passive,
        final_strategy=strat_leg,
        final_hodl=hodl,
        vol_oracle=vol_oracle,
    )

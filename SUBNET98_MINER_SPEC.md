# Subnet 98 — Miner Signal Specification

> ⚠️ **STATUS: RFC / NOT CURRENT BEHAVIOR.**
>
> This document describes a **proposed** future architecture (forecast-signal miners, CRPS scoring, ERC-4626 vaults, JIT-LP/flash-mint, executor reading aggregate ensembles). **None of this is implemented.**
>
> The live protocol today is the rebalance-only protocol described in [`MINER_GUIDE.md`](./MINER_GUIDE.md) and [`ARCHITECTURE.md`](./ARCHITECTURE.md): miners answer `RebalanceQuery` with `desired_positions` and are scored via `validator/services/scorer.py`. New miners should follow those docs, not this one.
>
> Kept here as a design reference. Treat as RFC.

---

## Overview

Miners no longer manage LP positions directly. Instead, they submit **forward-looking signals** that the validator scores and the executor consumes to drive a unified trading + LP-management strategy. The miner's job is purely predictive; execution is centralized at the executor side, ensuring uniform realism, fair scoring, and a single backtest harness across all miners.

This shifts the subnet from "best LP manager" to "best forecaster of pool dynamics."

---

## Architecture

The diagram below shows the four roles and how data flows between them. It's logical, not physical — one machine can host more than one role.

```
  ┌─────────┐  signals    ┌────────────┐  score + write   ┌────────────┐
  │ Miners  │ ──────────▶ │ Validators │ ───────────────▶ │ Signals DB │
  └─────────┘             └────────────┘                  └─────┬──────┘
                                                                │ read
                                                                ▼
  ┌──────────────┐  deposit/withdraw  ┌────────┐    tx    ┌──────────┐
  │ LP providers │ ─────────────────▶ │ Vaults │ ◀─────── │ Executor │
  └──────────────┘                    └────────┘          └──────────┘
```

### Miners — provide signals

Run any forecasting model they like (statistical, ML, on-chain heuristics, off-chain data). For each in-scope pool they publish a **distributional forecast** of the forward log-return over a fixed horizon — a fixed-length vector of quantiles. They compete on forecast calibration *and* sharpness — no execution, no capital, no on-chain integration.

### Validators — score signals

Consume miner signals as they're published. Once each forecast's horizon expires, compute the realized log-return from on-chain swap data and score the miner's distribution against the realized point using **CRPS** (Continuous Ranked Probability Score) — a proper scoring rule that rewards both well-calibrated *and* sharp forecasts. The score determines the miner's emission share. Validators also write the scored signals to a shared store (the **Signals DB**) tagged with the miner that produced them. The executor follows an **ensemble** of the top-N miners (validator-selected, stake-weighted), not a single miner — this damps individual streaks of luck.

### Executor — takes signals into account when rebalancing

A single executor instance, run and controlled by the team, polls the Signals DB each cadence period and submits the resulting vault txs. The current policy uses the signals as the primary inputs to the rebalance decision (see *Executor strategy* below), but signals are not the only input — the executor may layer other heuristics on top (e.g. on-chain pool state, gas conditions, vault inventory) and the policy can grow new legs (hedging, perps, alt sources of alpha) without changing the miner contract.

### LP providers — supply capital via vaults

End-users who want to earn the strategy's returns deposit into ERC-4626-style vaults. The vault holds the LP positions, accrues fees, and applies the executor's rebalances. LP providers receive vault shares; redemptions are settled at NAV.

---

## Signals miners submit

For each scoring round, every miner publishes a **predictive distribution** of the forward log-return at horizon `H` per pool they cover. The distribution is encoded as a fixed-length vector of quantiles.

### Quantile-based predictive distribution

```
Q̂ = [ q̂_05, q̂_10, q̂_25, q̂_50, q̂_75, q̂_90, q̂_95 ]
where  q̂_p = miner's predicted p-th percentile of  log(p(t+H) / p(t))
```

- **Type**: vector of 7 signed `float`s, monotonically non-decreasing
- **Units**: pure log-return; `+0.01` = +1%, `-0.005` = -0.5%
- **Required ordering**: `q̂_05 ≤ q̂_10 ≤ ... ≤ q̂_95` (validator rejects malformed submissions)
- **Quantile grid**: fixed at `{0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95}` — chosen to capture median (range center), IQR (range width), and tails (pull-out gate). Versioned, may be expanded later (e.g. add q_01 / q_99).

This single vector subsumes the prior scalar pair: the executor derives a point estimate `μ̂ = q̂_50` (median) and a spread estimate `σ̂ ≈ (q̂_75 − q̂_25) / 1.349` (IQR-to-stddev for a Gaussian; non-parametric callers can use the IQR directly). The distribution also exposes skew (asymmetry around the median) and tail risk (`|q̂_05|`, `|q̂_95|`), neither of which the old scalar pair captured.

The horizon `H` is set by the validator and shared across all miners.

---

## Miner scoring

After each signal's horizon expires, the validator computes the realized log-return from on-chain swap data:

```
μ_R = log(p(t+H) / p(t))
```

It then scores the submitted quantile distribution `Q̂` against the realized point `μ_R` using **CRPS** (Continuous Ranked Probability Score):

```
CRPS(Q̂, μ_R) = ∫ ( F̂(x) − 1{x ≥ μ_R} )² dx
```

where `F̂` is the empirical CDF defined by the quantile vector (linear interpolation between adjacent quantiles, flat tails outside the grid). Closed form for a discrete quantile grid is a finite sum — implementation is a few lines.

CRPS is a **proper** scoring rule: it cannot be gamed by widening the distribution (sharpness is rewarded) or by collapsing it onto a confident point (calibration is rewarded). It generalizes MAE to distributions and is the standard for ensemble-forecast leaderboards (ECMWF, Kaggle weather competitions, etc.).

The miner's score over a window of N signals is the mean CRPS plus a stale-or-missing penalty:

```
score = mean( CRPS_i for i in window )  +  λ × penalty_stale_or_missing
```

Lower score = better. The mapping from raw score to subnet rewards uses the standard linear-decay rank curve already in use elsewhere in subnet 98. The validator also uses these rankings to pick the top-N miners that the executor's ensemble follows.

---

## Executor strategy

The executor reads the latest **ensemble distribution** `Q̂` for each pool from the Signals DB (a stake-weighted aggregate of the top-N miners' quantile vectors, computed by the validator), and derives the inputs it needs:

```
μ̂ = q̂_50                              # median forecast → point estimate
σ̂ = (q̂_75 − q̂_25) / 1.349             # IQR-to-stddev → spread estimate
tail_down = |q̂_05|                     # 5th-pct downside risk
tail_up   =  q̂_95                      # 95th-pct upside
```

Each cadence tick (e.g. every `H/3` blocks):

### 1. Volatility-based pull-out gate

If the vault is currently in an LP position and `σ̂ > exit_vol_threshold` **OR** `tail_down > tail_exit_threshold`, **EXIT**: burn the LP, hold raw tokens. Skip the rest of the cycle.

If the vault is in cash and `σ̂ < reentry_vol_threshold`, proceed to step 2 to enter. Otherwise hold cash.

The vol band gives a soft hysteresis on aggregate spread; the tail gate adds a hard veto when the distribution's *downside* fattens — a property the old scalar `σ̂` couldn't see.

### 2. Price-anticipated range placement

When entering or rebalancing, the LP range center is the **predicted future tick**, not the current tick:

```
target_tick = current_tick + μ̂ / ln(1.0001)
```

Range bounds are skewed by the predicted distribution: lower edge tracks `q̂_10`, upper edge tracks `q̂_90`, both converted from log-returns to ticks and snapped to the pool's tick spacing:

```
tl = current_tick + q̂_10 / ln(1.0001)
tu = current_tick + q̂_90 / ln(1.0001)
w  = max(tu − tl, min_width_spacings × tick_spacing) / 2
```

This means an asymmetric forecast (e.g. `q̂_05 = -0.03, q̂_95 = +0.005`) produces an asymmetric range — wider on the downside where the distribution says price is more likely to travel — instead of the symmetric ±σ̂ band the old scalar pair forced.

### 3. Action selection

| current state | target range | action |
|---|---|---|
| in cash, gate open | `[tl, tu]` | **ENTER** at `[tl, tu]` |
| in position, target ≈ current | — | **HOLD** |
| in position, target far from current | `[tl', tu']` | **REBALANCE** (burn + swap-to-ratio + mint) |
| in position, gate triggered exit | — | **EXIT** to cash |

"Far" is defined by `|target_center − current_center| > rebalance_distance_ticks`.

### 4. Trade execution

ENTER and REBALANCE both require swapping idle balances into the LP's required token ratio at the new range, then minting. The executor uses a JIT-LP / flash-mint pattern so the pool fee paid on the rebalance swap is largely captured back by the LP it just minted, minimizing self-imposed friction.

EXIT is a clean burn to raw tokens — no swap.

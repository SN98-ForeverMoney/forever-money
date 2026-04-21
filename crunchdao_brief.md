# Volatility Prediction → LP Pull-Out Edge

## About SN98 (ForeverMoney)

We operate **Subnet 98 ("ForeverMoney")** on the Bittensor network — a
decentralized AI / strategy marketplace where independent operators
("miners") compete on a well-defined task, validators score them on
real out-of-sample performance, and the network distributes
token-denominated incentives proportional to that score.

The task SN98 is built around is **automated concentrated-liquidity
management on EVM L2s** (currently Base; other chains in scope). We
see two distinct revenue lines that share the same underlying
research and infrastructure:

### Product 1 — Blue-chip LP optimization (yield product)

Run capital — own and third-party — as concentrated liquidity on
deep, high-volume pairs (WETH/USDC, WETH/cbBTC, USDC/cbBTC, …) and
**maximize realized APY net of LVR, target: consistently beat HODL.**

The competitive edge here is exactly the LVR-vs-fees tradeoff
described below. Predictive signals on volatility (and eventually
price) are the lever that lets us shift capital out of high-LVR
windows and into high-fee-yield windows. We expect this product to be
positioned to LPs and treasuries as a managed-yield vehicle on top
of Base/Aerodrome.

### Product 2 — Protocol-Owned Liquidity as a service (B2B)

Provide active LP management to other Base-native protocols that
need to bootstrap or maintain market depth for their own tokens —
specifically including the **xSN-* alpha tokens** of other Bittensor
subnets, which currently trade in thin pools with painful slippage.

The same predictive infrastructure powers a different objective here:
instead of maximizing return for the LP, we maximize **trader UX**
(tight spreads, low price-impact, deep displayed liquidity). The
incentive structure is fee-share / management-fee / token-aligned
rather than spot APY. POL-as-a-service is meaningful because most
small protocols don't have the engineering or risk infrastructure to
do this in-house, and the savings (vs. the slippage their users would
otherwise eat) are substantial.

Both products consume the same primitive: **better forward-looking
information about pool dynamics → better LP decisions.

## Background: what LVR is

**LVR (Loss-Versus-Rebalancing)**, sometimes also called *arb leakage* or
*adverse selection cost*, is the dominant structural cost of providing
liquidity to constant-function AMMs (Uniswap V2/V3, Aerodrome, etc.).

Mechanically: an AMM's pool price only updates via swaps. When the
"true" external price (e.g. on a CEX) moves, the AMM is mispriced
until an arbitrageur trades against it to bring it back in line. The
arbitrageur's profit on that trade comes out of the LP's pocket.

Formally, for a pool with marginal price `P(t)` and a liquidity
provider with infinitesimal share `dL`, Milionis et al. (2022) show that
in the continuous-time limit the per-unit-time LVR rate equals:

```
ℓ(t) = (1/2) · σ²(t) · P(t) · |∂x/∂P|
```

where `σ²(t)` is the instantaneous variance of the log-price. The integral
of `ℓ(t)` is what the LP loses to arbitrageurs every period the price
moves, *regardless of whether the LP earns fees or not*. Fee income is
the LP's compensation; LVR is the cost. A position is profitable iff
`fees > LVR`.

For Uniswap V3 the analysis is stricter: LVR scales with `σ²` AND the
LP pays it only while the price is **inside** the active range (out of
range, the LP holds a single asset and is no longer marginal). This means:

- **High-volatility windows ⇒ high LVR ⇒ LP loses money even with strong fee income.**
- **Low-volatility windows ⇒ low LVR ⇒ fees dominate ⇒ LP makes money.**

Empirically on Base WETH/USDC, LVR routinely exceeds fee income on
2-4σ vol days. A passive narrow-range LP that ignores vol structure
underperforms HODL by 15-25% over 90-day windows.

The most direct mitigation is *being out of the pool when LVR is high
and in it when fees dominate.* That is the strategy below.

## The system

We're building a liquidity-management system on Base. One component
asks: **if we can predict realized volatility one window ahead, do we
get a P&L edge by exiting the LP during predicted high-vol periods and
re-entering during calm?**

We have a backtester that scores this hypothesis against historical
swap data. Right now the backtester feeds the strategy a synthetic
predictor with controlled noise so we can measure the accuracy/edge
function. We want to replace that synthetic predictor with a real one
from CrunchDAO.

## The strategy (one of several being tested)

A `volatility_miner` LP holds a single concentrated-liquidity position
of width proportional to recent realized vol, recentered when price
drifts toward the position's edge.

We layer a **pull-out hysteresis** on top:

```
predicted_vol > exit_threshold        → EXIT  (burn position, hold raw tokens)
out & predicted_vol < reentry         → ENTER (re-mint position)
   (exit_threshold > reentry: prevents thrashing)
```

All actions assume a 15-block detect→tx→broadcast latency, V3-accurate
slippage, and pool fees.

## What we're seeing so far (with synthetic predictor)

We've run the strategy through the backtester on Base WETH/USDC and
related pools, sweeping over (period × predictor noise × pull-out
threshold). Predictor noise is set as `σ_noise ∈ {0.20, 0.30, 0.50}` —
i.e. 20–50% multiplicative error against ground-truth `σ_R`. Slippage
is V3-accurate plus 2 bps non-ideality buffer.

**Headline numbers (excess return vs HODL, best cell per window):**

| Period | Pool                 | Best excess | Strategy | HODL    | Predictor noise |
|--------|----------------------|------------:|---------:|--------:|-----------------|
| 30d    | WETH/USDC (0xd0b5…)  | **+1.0%**   | +6.6%    | +5.6%   | σ_noise = 0.30  |
| 60d    | WETH/USDC (0xd0b5…)  | **+5.7%**   | −8.9%    | −14.6%  | σ_noise = 0.20  |
| 90d    | WETH/USDC (0xd0b5…)  | **+8.4%**   | −6.8%    | −15.2%  | σ_noise = 0.50  |
| 120d   | WETH/USDC (0xd0b5…)  | **+12.2%**  | −3.5%    | −15.8%  | σ_noise = 0.20  |
| 120d   | WETH/USDC (0xb2cc…)  | **+11.7%**  | −4.1%    | −15.8%  | σ_noise = 0.30  |

Across the full 60-cell sweep on 0xd0b5… 120d, **36 of 60 cells beat
HODL** — meaning the edge is robust across a wide range of (noise,
exit-threshold) settings, not a knife-edge optimum. On the second
WETH/USDC pool (0xb2cc…) we got **75 of 120 winners** in the all-pool
extended sweep.

**What this tells us:**

1. **Even an intentionally noisy predictor (20–50% error) generates a
   real and recoverable edge** in bear/high-vol windows. Excess scales
   with horizon length (more time for LVR to compound on the HODL/passive
   benchmarks, more opportunities for the strategy to step out).
2. **The edge is regime-dependent.** Uptrending windows (e.g. WETH/USDC
   30d, USDC/cbBTC 60-90d) are mostly unwinnable — directional drift
   dominates and any rebalancing LP underperforms HODL on the
   appreciating asset. Bear/sideways/high-vol windows are where the
   strategy harvests value.
3. **Pool depth matters more than fee tier.** The 5 bps and 30 bps
   WETH/USDC pools both produce wins; thin alpha-token pools
   (xSN-*/USDC) produce 0 winners across hundreds of cells —
   expected, since position-vs-pool size makes any rebalance
   self-sandwiching.
4. **A better predictor improves both magnitude and robustness.**
   Our σ_noise=0.20 cells already beat HODL by 5–12% in bear windows;
   a calibrated, narrow-error predictor should both raise the ceiling
   and widen the band of winning configs.

These results are why we're prioritizing the volatility predictor as
the first signal to go live in the production system. 

## What we need from CrunchDAO

### Volatility prediction (now)

For a target pool, at decision time `t`, with horizon `H` blocks:

> **`σ̂_R(t, t+H)`** — point estimate (or distribution) of forward
> realized volatility over `[t, t+H]`.

**The σ_R definition we use** (so the predictor and our scoring agree):

```
For bucket size B blocks (default B = 30):
  bucket_end_i = t + i·B,    i = 1..⌈H/B⌉
  p_i          = sqrt_price_x96 of the latest swap with block ≤ bucket_end_i
  r_i          = log(p_i / p_{i-1})
  σ_R(t, t+H)  = std-dev({r_i})
```

We default `H = 300` blocks (≈ 10 minutes on Base, which has 2-second
blocks). Other horizons in scope: `60, 300, 900, 1800` blocks.

**Output schema (one row per decision time `t`):**

| Field                | Type   | Notes                                    |
|----------------------|--------|------------------------------------------|
| pool_address         | hex    | The pool the prediction is for           |
| block_number         | int    | Decision time (`t`)                      |
| horizon_blocks       | int    | `H`                                      |
| sigma_hat            | float  | Point estimate of σ_R(t, t+H)            |
| sigma_hat_lo         | float? | Optional lower CI                        |
| sigma_hat_hi         | float? | Optional upper CI                        |
| model_id             | str    | Versioning — see below                   |
| training_cutoff_block| int    | Latest block used to train this model    |

**If you can give us the conditional distribution** (or first 2 moments
+ a tail measure like `P(σ > θ)` for a few `θ`), even better. It lets
us swap the binary threshold for an expected-utility decision rule.

### Quality reporting (so we can replace our noise model)

Right now our backtester models predictor quality as multiplicative
Gaussian noise:

```
predicted_vol(t) = σ_R(t, t+H) × (1 + ε),    ε ~ N(0, σ_noise)
```

We sweep `σ_noise ∈ {0.20, 0.30, 0.50}` and measure how strategy P&L
degrades. To replace this with your actual error structure, we'd want:

1. **Out-of-sample error metrics**: RMSE, MAE on σ_R; bias
   `E[σ̂ − σ_R]`.
2. **Multiplicative error distribution**: histogram of `(σ̂/σ_R − 1)` —
   confirms or rejects our Gaussian assumption.
3. **Calibration**: decile-binned predicted vs realized.
4. **Regime breakdown**: error in low-vol vs high-vol regimes (these
   are asymmetrically costly — over-predicting vol in calm periods
   costs us fees; under-predicting in turbulent periods costs us LVR).

### Price prediction (future)

Once vol works, we'll move on to **expected return** (and ideally a
predictive distribution) over the same horizon `H`. That unlocks:

- **Asymmetric range placement** — center the LP slightly above/below
  current tick to lean into expected drift.
- **Conditional exits** — exit if expected drawdown exceeds expected
  fee accrual (a tighter rule than vol-only pull-out).
- **Off-pool hedging** — short the underlying to neutralize directional
  exposure while keeping fee accrual.

**Likely output schema:**

| Field                  | Type   |
|------------------------|--------|
| pool_address           | hex    |
| block_number           | int    |
| horizon_blocks         | int    |
| mu_log_return          | float  | E[log(p_{t+H} / p_t)]                |
| sigma_log_return       | float? | predictive std                       |
| p_up                   | float? | P(p_{t+H} > p_t)                     |
| skew, kurtosis         | float? | for non-Gaussian conditional dist    |
| model_id, training_cutoff_block as above |

## Practical asks

1. **Historical predictions** API (or batch dump) keyed by
   `(pool, block, horizon)` so we can replay across the backtest window.
2. **Streaming endpoint** for live use; document the prediction lag
   (blocks between availability and decision time). We have ≈ 15
   blocks of end-to-end latency budget.
3. **`(model_id, training_cutoff_block)`** on every output so we can
   prevent lookahead leak when backtesting.
4. **A sample dataset for one pool** to validate end-to-end. Suggest:

   - **Pool**: WETH/USDC `0xd0b53d9277642d899DF5C87A3966A349A798F224` (Aerodrome CL on Base)
   - **Window**: last 120 days
   - **Horizon**: H = 300 blocks
   - **Cadence**: a prediction every 30 blocks (matching our bucket size)


## Open questions for the call

- Bucket size — is 30 blocks reasonable for your model architecture?
  Smaller buckets = noisier samples but higher temporal resolution.
- Would you rather optimize for **point accuracy on σ_R** or directly
  for **excess return on our backtester**? We can wire either as the
  loss function.
- For the conditional distribution: parametric (e.g. log-normal with
  predicted σ) or non-parametric (quantile predictions)?

# SN98 ForeverMoney - Miner Implementation Guide

## Overview

This guide shows you how to implement your own liquidity management strategy as a miner on SN98 ForeverMoney. Miners compete to provide the best dynamic rebalancing decisions for Uniswap V3 / Aerodrome liquidity positions.

## How Mining Works

### The Basics

1. **Validators run jobs** for different liquidity pools (e.g., ETH/USDC, WBTC/USDC)
2. **Validators query you** during forward simulations starting from current chainhead (live blockchain state)
3. **You respond** with rebalancing decisions (keep current positions or rebalance to new positions)
4. **You get scored** based on expected performance over the round duration (fees, impermanent loss, etc.)
5. **Winners get selected** for live on-chain execution after consistent participation (default: 7 days)

### What You Receive (RebalanceQuery)

During a forward simulation (starting from current chainhead), validators send you:

```python
RebalanceQuery {
    # Job context
    job_id: str                           # Unique job identifier
    sn_liquidity_manager_address: str     # Vault address
    pair_address: str                     # Pool address (e.g., ETH/USDC)
    chain_id: int                         # 8453 for Base
    round_id: str                         # Round identifier
    round_type: str                       # 'evaluation' or 'live'

    # Current state
    block_number: int                     # Current block in simulation
    current_price: float                  # Current price (token1/token0)
    current_positions: List[Position]     # Active LP positions
    inventory_remaining: Optional[dict]   # {"amount0": str, "amount1": str} idle tokens
    rebalances_so_far: int                # Rebalances executed so far in this round
    tick_spacing: int                     # Pool tick spacing — snap your tick_lower / tick_upper to this
}
```

`current_positions` and `desired_positions` are lists of `Position` objects:

```python
Position { tick_lower: int, tick_upper: int, allocation0: str, allocation1: str }
```

`allocation0` / `allocation1` are uint256 wei strings (use string to avoid JSON precision loss).

### What You Must Return

Populate these fields on the **same synapse**:

```python
RebalanceQuery {
    accepted: bool                        # True to accept job, False to refuse
    refusal_reason: Optional[str]         # Reason if refusing
    desired_positions: Optional[List[Position]]
                                          # Required if accepted=True.
                                          # Return current_positions to keep them unchanged.
                                          # None reads as "no response / timeout" — penalised.
    miner_metadata: MinerMetadata         # Your version and model info
}
```

**Rebalance semantics:** the executor reconciles `desired_positions` against on-chain state with a 2% tick-tolerance. Existing positions whose ticks don't match any desired position (within 2%) are **burned**. New positions are minted from idle inventory. Returning `current_positions` unchanged is a no-op.

## Implementation Guide

### Required handlers

Your axon must serve **two** synapses:

1. `RebalanceQuery` — strategy decisions (covered below).
2. `VaultRegistrationQuery` — tells the validator which on-chain `SnLiquidityManager` (vault) you manage.

Without `VaultRegistrationQuery`, the validator's vault-eligibility filter (`round_orchestrator.py`) will skip you for every job and you will never be queried for `RebalanceQuery`. **You will not earn.**

```python
# miner/miner.py
from protocol.synapses import VaultRegistrationQuery

async def vault_registration_handler(self, synapse: VaultRegistrationQuery) -> VaultRegistrationQuery:
    synapse.has_vault = True
    synapse.vault_address = self.vault_address      # your deployed SnLiquidityManager
    synapse.chain_id = 8453                         # Base
    return synapse
```

The reference miner deploys a vault at startup (or reads `MINER_VAULT_ADDRESSES` from `.env`). See `MINER_REGISTRATION_GUIDE.md`.

### Step 1: Basic Handler Structure

The minimal `RebalanceQuery` handler looks like this:

```python
# miner/miner.py

async def rebalance_query_handler(self, synapse: RebalanceQuery) -> RebalanceQuery:
    """
    Handle RebalanceQuery from validators.

    This is where you implement your strategy!
    """
    try:
        # 1. Decide if you want to work on this job
        if not self._should_accept_job(synapse):
            synapse.accepted = False
            synapse.refusal_reason = "Not working on this pair"
            synapse.desired_positions = []  # Empty list when refusing
            synapse.miner_metadata = MinerMetadata(
                version="1.0.0",
                model_info="My Strategy v1"
            )
            return synapse

        # 2. Accept the job
        synapse.accepted = True
        synapse.refusal_reason = None

        # 3. Decide if you want to rebalance
        should_rebalance, new_positions, reason = self._decide_rebalance(synapse)

        if should_rebalance:
            synapse.desired_positions = new_positions
        else:
            # Keep current positions by returning them as desired
            synapse.desired_positions = synapse.current_positions

        # 4. Add metadata
        synapse.miner_metadata = MinerMetadata(
            version="1.0.0",
            model_info="My Strategy v1"
        )

        return synapse

    except Exception as e:
        logger.error(f"Error in rebalance handler: {e}", exc_info=True)
        # Return safe default: accept but keep current positions
        synapse.accepted = True
        synapse.desired_positions = synapse.current_positions
        synapse.miner_metadata = MinerMetadata(version="1.0.0", model_info="Error")
        return synapse
```

### Step 2: Job Filtering (Optional)

Decide which jobs you want to work on:

```python
def _should_accept_job(self, synapse: RebalanceQuery) -> bool:
    """
    Filter jobs based on your preferences.

    Examples:
    - Only work on specific pairs
    - Only work on evaluation rounds
    - Only work on certain vaults
    """
    # Example 1: Only work on ETH pairs
    if "eth" not in synapse.pair_address.lower():
        return False

    # Example 2: Skip if too many rebalances already
    if synapse.rebalances_so_far >= 5:
        return False

    # Example 3: Only work on evaluation (safer)
    if synapse.round_type == "live":
        return False  # Not ready for live yet

    return True
```

### Step 3: Implement Your Strategy

This is where you compete! 


## Understanding Scoring

Authoritative source: [`validator/services/scorer.py`](validator/services/scorer.py).

### The signal

Score is a **relative return** clamped to a bounded range, multiplied by an inventory-loss penalty, optionally lifted by an in-range bonus.

```python
# 1. Relative return vs initial
return_pct = (final_value - initial_value) / initial_value
return_pct = clamp(return_pct, -10.0, 10.0)

# 2. Loss penalty — prefers backtester-reported impermanent_loss,
#    falls back to max(loss_ratio_token0, loss_ratio_token1).
loss_ratio = metrics.get("impermanent_loss") or fallback_token_delta_loss
penalty    = exp(-10.0 * loss_ratio)             # 10 = DEFAULT_LOSS_PENALTY

# 3. Symmetric: gains are scaled down by penalty, losses are amplified
score = return_pct * penalty if return_pct >= 0 else return_pct / penalty

# 4. Optional in-range bonus (DEFAULT_IN_RANGE_WEIGHT = 0.08)
r = clamp(metrics.get("in_range_ratio", 0), 0, 1)
score *= (1 - 0.08) + 0.08 * r

# 5. Final clamp to JSON/DB-safe range
score = clamp(score, SCORE_MIN=-100.0, SCORE_MAX=10.0)
```

A failed live execution (executor bot returns non-200, on-chain revert) bypasses this and gets `score = -100`.

### Worked numbers

| Scenario | return_pct | loss_ratio | penalty | in_range | raw | clamped |
|---|---:|---:|---:|---:|---:|---:|
| +5% return, 0 loss, 100% in-range | 0.05 | 0.00 | 1.000 | 1.0 | 0.0500 | 0.0500 |
| +5% return, 0 loss, 50% in-range | 0.05 | 0.00 | 1.000 | 0.5 | 0.0480 | 0.0480 |
| +5% return, 5% IL, 80% in-range | 0.05 | 0.05 | 0.607 | 0.8 | 0.0298 | 0.0298 |
| +5% return, 10% IL, 80% in-range | 0.05 | 0.10 | 0.368 | 0.8 | 0.0181 | 0.0181 |
| -5% return, 5% IL, 50% in-range | -0.05 | 0.05 | 0.607 | 0.5 | -0.0791 | -0.0791 |
| -5% return, 10% IL, 50% in-range | -0.05 | 0.10 | 0.368 | 0.5 | -0.1305 | -0.1305 |
| failed live execution | n/a | n/a | n/a | n/a | n/a | **-100** |

### Takeaways

- **The penalty multiplier is 10**, applied to the raw IL ratio (not a percentage). 5% IL ⇒ penalty=0.61, 10% IL ⇒ 0.37, 20% IL ⇒ 0.14.
- **Penalty is symmetric.** Gains shrink, losses grow. Inventory loss hurts both directions.
- **In-range bonus is small** (max +8% on score) — don't sacrifice IL to chase it.
- **Scores live in `[-100, +10]`.** A round score of `1.0` is huge. A round score of `-100` is a failed execution.
- **Miss/timeout/refusal** ⇒ `score=0` for the round (not `-100`). `-100` is reserved for accepted-then-failed.

### Score updates (EMA) and ranking

```python
# Per validator round (authoritative: validator/repositories/job.py SCORE_UPDATE log line)
new_eval_score = 0.9 * old_eval_score + 0.1 * latest_eval_score
new_live_score = 0.7 * old_live_score + 0.3 * latest_live_score
combined_score = 0.6 * eval_score + 0.4 * live_score
```

Recent rounds weigh more; live rounds weigh more than eval rounds. Self-monitoring tip — grep your validator logs for the canonical line:

```
[SCORE_UPDATE] miner=<uid> job=<job_id> eval=<x> live=<y> combined=<z> total_evals=<n> successful=<m> refusals=<r>
```

### Eligibility for live rounds

A miner is only routed to **live execution** after `MINER_ELIGIBILITY_DAYS` of consistent evaluation participation (env var on the validator; default 7, **production currently runs at 1**). Until then, you only see evaluation rounds.

## Round Cadence

The validator runs both an **evaluation** and a **live** round per job, then sleeps for **4 hours** before the next pair (`validator/round_orchestrator.py:run_job_continuously`). One round pair takes ~10–15 min wallclock. So your miner will be queried roughly every 4 hours per job.

Scoring updates happen on every round; live execution only happens once you're eligibility-cleared (see "Eligibility for live rounds" above).

## Running Your Miner

### 1. Setup

```bash
pip install -r requirements.txt

export WALLET_NAME=your_wallet
export HOTKEY_NAME=your_hotkey
export SUBTENSOR_NETWORK=finney   # finney = mainnet, test = testnet
export NETUID=98                  # 98 mainnet, 374 testnet

# Vault you manage (required — see MINER_REGISTRATION_GUIDE.md)
export MINER_VAULT_ADDRESSES='["0xYourLiquidityManagerAddress"]'
export MINER_VAULT_CHAIN_ID=8453

# Optional: historical pool-events DB for backtesting your strategy
export DB_CONNECTION_STRING=postgresql+asyncpg://user:pass@host:port/pool_events
```

### 2. Run

```bash
python -m miner.miner \
  --wallet.name $WALLET_NAME \
  --wallet.hotkey $HOTKEY_NAME
```

### 3. Monitor

```bash
tail -f miner.log
```

Look for:
- `VaultRegistrationQuery` received and answered with `has_vault=True`
- `RebalanceQuery` received per round
- Your accept/refuse decisions and computed positions
- Any axon errors

To track your scores from the validator side, grep the validator logs for `[SCORE_UPDATE] miner=<your_uid>`.

**Good luck and happy mining! 🚀**

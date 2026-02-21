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
    round_id: str                         # Current round ID
    round_type: str                       # 'evaluation' or 'live'

    # Current state
    block_number: int                     # Current block in simulation
    current_price: float                  # Current price (token1/token0)
    current_positions: List[Position]     # Active LP positions
    inventory_remaining: Inventory        # Available tokens

    # Historical context
    rebalances_so_far: int               # Number of rebalances in this round
    # ... other context fields
}
```

### What You Must Return

Populate these fields on the **same synapse**:

```python
RebalanceQuery {
    # Required fields (you populate these)
    accepted: bool                        # True to accept job, False to refuse
    refusal_reason: Optional[str]         # Reason if refusing
    desired_positions: List[Position]     # Desired positions (required if accepted=True)
                                          # Return current_positions to keep them unchanged
    miner_metadata: MinerMetadata         # Your version and model info
}
```

## Implementation Guide

### Step 1: Basic Handler Structure

The minimal miner handler looks like this:

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

**⚠️ IMPORTANT: Current Scoring Mechanism (PoL Target)**

This scoring function applies to the **current implementation** where all jobs use the **"PoL" (Protocol Owned Liquidity)** target. In the future, each job will have its own target type (e.g., "MaxFees", "MinIL", "Balanced"), and scoring will be determined by the job's specific target. For now, all jobs optimize for Protocol Owned Liquidity inventory protection.

---

### The Scoring Function

Your strategy is scored based on two critical factors:

1. **Value Growth** - Maximize portfolio value based on pool price appreciation and fees (primary signal)
2. **Inventory Protection** - Protect initial token amounts through exponential penalty for losses

The algorithm uses a smooth exponential penalty that:
- **Reduces positive gains** when inventory is lost
- **Amplifies negative losses** when inventory is lost
- **No penalty** when all tokens are preserved

### How It Works

#### 1. **Calculate Value Gain** (Primary Signal)
```python
# Initial value (in token1 units, at initial price)
initial_value = (initial_amount0 × initial_price) + initial_amount1

# Final value (in token1 units, at final price, including fees)
final_value = (final_amount0 × final_price) + final_amount1 + fees

# Value gain (can be positive or negative)
value_gain = final_value - initial_value
```
- All values in token1 units using pool price
- Includes all fees earned
- This is your base score before penalty

#### 2. **Measure Relative Inventory Losses**
```python
# Percentage of each token lost
loss_ratio0 = (initial_amount0 - final_amount0) / initial_amount0
loss_ratio1 = (initial_amount1 - final_amount1) / initial_amount1

# Examples:
# Lost 0 tokens → loss_ratio = 0.0 (0%)
# Lost 10% of tokens → loss_ratio = 0.1 (10%)
# Lost 50% of tokens → loss_ratio = 0.5 (50%)
```
- Measures **percentage** of initial inventory lost
- Calculated separately for each token
- Zero if token amount increased

#### 3. **Aggregate Losses with Smooth-Max**
```python
# Smooth-max combines both loss ratios (like max but differentiable)
inventory_loss_ratio = smooth_max(loss_ratio0, loss_ratio1)

# Approximates max(loss_ratio0, loss_ratio1) but considers both
```
- Uses log-sum-exp aggregation
- Focuses on the **worse** loss but considers both
- Smooth and always rankable

#### 4. **Apply Exponential Penalty**
```python
# Exponential penalty factor (default multiplier = 10)
penalty_factor = exp(-10 × inventory_loss_ratio)

# Examples:
# 0% loss  → penalty_factor = exp(0) = 1.000 (no penalty)
# 5% loss  → penalty_factor = exp(-0.5) ≈ 0.606
# 10% loss → penalty_factor = exp(-1.0) ≈ 0.368
# 20% loss → penalty_factor = exp(-2.0) ≈ 0.135
# 50% loss → penalty_factor = exp(-5.0) ≈ 0.007
```
- Penalty grows **exponentially** with inventory loss
- Even small losses create significant penalty
- Large losses nearly eliminate your score

#### 5. **Symmetric Penalty Application**
```python
if value_gain >= 0:
    # Positive gains → multiply by penalty (reduces gain)
    score = value_gain × penalty_factor
else:
    # Negative losses → divide by penalty (amplifies loss)
    score = value_gain / penalty_factor
```

**Why symmetric?**
- Losing inventory while gaining value → gain is reduced
- Losing inventory while losing value → loss is amplified
- Either way, inventory loss hurts!

### Practical Examples

#### Example 1: Perfect Strategy - No Inventory Loss ✅✅✅
```python
# Initial
initial_amount0 = 1000 tokens
initial_amount1 = 2000 tokens
initial_value = $12,000

# Final (no token losses!)
final_amount0 = 1000 tokens  # Preserved ✅
final_amount1 = 2000 tokens  # Preserved ✅
final_value = $14,200 (includes $200 fees)

# Scoring
loss_ratio0 = 0.0
loss_ratio1 = 0.0
inventory_loss_ratio = 0.0
penalty_factor = exp(0) = 1.0

value_gain = 14,200 - 12,000 = 2,200
score = 2,200 × 1.0 = 2,200 ✅✅✅
```

#### Example 2: Good Gains BUT Lost 10% of Token0 ⚠️
```python
# Initial
initial_amount0 = 1000 tokens
initial_amount1 = 2000 tokens
initial_value = $12,000

# Final (lost 10% of token0)
final_amount0 = 900 tokens   # Lost 10%! ❌
final_amount1 = 2000 tokens  # Preserved
final_value = $15,800 (price up + fees)

# Scoring
loss_ratio0 = (1000 - 900) / 1000 = 0.1 (10%)
loss_ratio1 = 0.0
inventory_loss_ratio ≈ 0.1
penalty_factor = exp(-10 × 0.1) = exp(-1) ≈ 0.368

value_gain = 15,800 - 12,000 = 3,800
score = 3,800 × 0.368 ≈ 1,398 ⚠️

# Gained $3,800 but lost 10% tokens → score reduced by 63%!
```

#### Example 3: Lost Value AND Lost 10% Tokens ❌❌❌
```python
# Initial
initial_amount0 = 1000 tokens
initial_amount1 = 2000 tokens
initial_value = $12,000

# Final (bad all around)
final_amount0 = 900 tokens   # Lost 10%! ❌
final_amount1 = 2000 tokens
final_value = $11,000 (price down)

# Scoring
loss_ratio0 = 0.1
inventory_loss_ratio ≈ 0.1
penalty_factor = exp(-1) ≈ 0.368

value_gain = 11,000 - 12,000 = -1,000
score = -1,000 / 0.368 ≈ -2,717 ❌❌❌

# Lost $1,000 AND lost 10% tokens → loss amplified by 2.7x!
```

#### Example 4: Lost 50% of Tokens (Catastrophic) ☠️
```python
# Even with positive value gain
loss_ratio = 0.5
penalty_factor = exp(-10 × 0.5) = exp(-5) ≈ 0.0067

value_gain = 5,000  # Good gain!
score = 5,000 × 0.0067 ≈ 33.5 ☠️

# $5,000 gain → reduced to $33 due to 50% inventory loss!
```

### Key Takeaways for Miners

#### 1. **PROTECT YOUR INVENTORY** 🛡️
- Even **5-10% token loss** severely impacts score
- 10% loss → 63% score reduction
- 50% loss → 99% score reduction
- **Zero tolerance** for inventory loss!

#### 2. **The Penalty is Exponential** 📉
- Small losses (5%) → moderate penalty
- Medium losses (10-20%) → severe penalty
- Large losses (>30%) → catastrophic penalty
- **Non-linear** - gets worse fast!

#### 3. **Penalty Applies Both Ways** ⚔️
- Gains + loss → gains reduced
- Losses + inventory loss → losses amplified
- **Double punishment** when both go wrong

#### 4. **Focus on Preservation First** 🎯
- Better to preserve inventory with small gain
- Than to chase high gains and lose tokens
- Wide ranges, conservative rebalancing
- **Capital preservation >> aggressive fees**

### Score Updates (Exponential Moving Average)

Your scores are updated after each round using EMA:

```python
# After evaluation round
new_eval_score = old_eval_score × 0.9 + latest_score × 0.1

# After live round (if eligible)
new_live_score = old_live_score × 0.7 + latest_score × 0.3

# Combined score (used for ranking)
combined_score = (eval_score × 0.6) + (live_score × 0.4)
```

- **Recent performance matters more** (EMA gives higher weight to new results)
- **Live rounds count more** than evaluation (0.4 vs 0.6 weight)
- **Consistency pays off** - one bad round won't kill your score

### Future: Target-Based Scoring

In future versions, each job will specify its target optimization goal:

- **"PoL"** (current): Protocol Owned Liquidity - maintain 50/50 balance
- **"MaxFees"**: Maximize fee collection (may allow more imbalance)
- **"MinIL"**: Minimize impermanent loss (wider ranges)
- **"Balanced"**: Balance between fees and IL

Miners will be able to specialize in different target types across jobs.

## Running Your Miner

### 1. Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export WALLET_NAME=your_wallet
export HOTKEY_NAME=your_hotkey
export SUBTENSOR_NETWORK=finney  # or test/local
export NETUID=98

# Optional: Historical data access
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
# Watch logs
tail -f miner.log

# Look for:
# - RebalanceQuery received
# - Your decisions (rebalance/keep)
# - Any errors
```

**Good luck and happy mining! 🚀**

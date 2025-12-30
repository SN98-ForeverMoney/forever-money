# SN98 ForeverMoney - Miner Implementation Guide

## Overview

This guide shows you how to implement your own liquidity management strategy as a miner on SN98 ForeverMoney. Miners compete to provide the best dynamic rebalancing decisions for Uniswap V3 / Aerodrome liquidity positions.

## How Mining Works

### The Basics

1. **Validators run jobs** for different liquidity pools (e.g., ETH/USDC, WBTC/USDC)
2. **Validators query you** during forward simulations starting from current chainhead (live blockchain state)
3. **You respond** with rebalancing decisions (keep current positions or rebalance to new positions)
4. **You get scored** based on expected performance over the round duration (fees, impermanent loss, etc.)
5. **Winners get selected** for live on-chain execution after 7 days of participation

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

This scoring function applies to the **current implementation** where all jobs use the **"PoL" (Protocol Owned Liquidity)** target. In the future, each job will have its own target type (e.g., "MaxFees", "MinIL", "Balanced"), and scoring will be determined by the job's specific target. For now, all jobs optimize for Protocol Owned Liquidity balance.

---

### The Scoring Function

Your strategy is scored based on three key factors:

1. **Total Value** - The combined value of your token holdings (amount0_holdings + amount1_holdings)
2. **Ratio Discipline** - How well you maintain the target 50/50 balance between tokens
3. **Fees Collected** - The total fees earned from liquidity provision

The scoring function calculates your total portfolio value, applies a penalty for deviation from the 50/50 target ratio, and adds a small bonus for fees collected. The goal is to maintain a balanced portfolio while preserving capital and earning fees.

### How It Works

#### 1. **Total Value** (Primary Driver)
```python
total_value = amount0_holdings + amount1_holdings
```
- All values converted to same unit (token1/USD equivalent)
- Higher total value = better (assuming similar balance)

#### 2. **Ratio Discipline** (Critical!)
```python
target_ratio = 0.5  # 50% token0, 50% token1
actual_ratio = amount0_holdings / total_value
ratio_error = abs(actual_ratio - target_ratio) / target_ratio

# Example:
# Perfect balance: 50/50 → ratio_error = 0.0 → penalty = 1.0
# Moderate drift:  60/40 → ratio_error = 0.2 → penalty = 0.96
# Heavy drift:     80/20 → ratio_error = 0.6 → penalty = 0.74
# Extreme drift:   90/10 → ratio_error = 0.8 → penalty = 0.61
```

**The ratio penalty is STRONG:**
- Maintains 50/50: `penalty = 1.0` ✅
- Drifts to 60/40: `penalty ≈ 0.96` ⚠️
- Drifts to 70/30: `penalty ≈ 0.84` ❌
- Drifts to 80/20: `penalty ≈ 0.74` ❌❌

#### 3. **Core Score** (Capital × Balance)
```python
core_score = total_value * ratio_penalty
```
- Combines capital preservation with balance quality
- You can have high capital BUT if imbalanced, score suffers
- You can be perfectly balanced BUT if lost capital, score suffers

#### 4. **Fees Bonus** (Secondary)
```python
fee_weight = 0.1  # Fees are 10% weight
score = core_score + (0.1 × fees_collected)
```
- Fees provide a **small boost** to your score
- But they DON'T compensate for poor balance or capital loss
- Prioritize balance and capital preservation first!

### Practical Examples

#### Example 1: Perfect Strategy ✅
```python
# Started with: $10,000 (50/50)
# Ended with:   $10,500 (50/50) + $200 fees

amount0_holdings = 5,250  # 50%
amount1_holdings = 5,250  # 50%
fees_collected = 200

total_value = 10,500
actual_ratio = 5,250 / 10,500 = 0.5  # Perfect!
ratio_error = 0.0
ratio_penalty = 1.0

core_score = 10,500 × 1.0 = 10,500
score = 10,500 + (0.1 × 200) = 10,520 ✅
```

#### Example 2: High Fees, Bad Balance ❌
```python
# Started with: $10,000 (50/50)
# Ended with:   $10,200 (80/20) + $500 fees (from narrow range)

amount0_holdings = 8,160  # 80% - IMBALANCED!
amount1_holdings = 2,040  # 20%
fees_collected = 500

total_value = 10,200
actual_ratio = 8,160 / 10,200 = 0.8
ratio_error = |0.8 - 0.5| / 0.5 = 0.6
ratio_penalty = 1 / (1 + 0.6²) = 1 / 1.36 ≈ 0.74

core_score = 10,200 × 0.74 = 7,548
score = 7,548 + (0.1 × 500) = 7,598 ❌

# Despite higher fees and higher total value,
# the imbalance penalty kills the score!
```

#### Example 3: Good Balance, Capital Loss ❌
```python
# Started with: $10,000 (50/50)
# Ended with:   $9,000 (50/50) + $100 fees

amount0_holdings = 4,500  # 50% - Good balance
amount1_holdings = 4,500  # 50%
fees_collected = 100

total_value = 9,000
actual_ratio = 0.5
ratio_penalty = 1.0

core_score = 9,000 × 1.0 = 9,000
score = 9,000 + (0.1 × 100) = 9,010 ❌

# Good balance but lost capital (IL or poor positioning)
```

### Key Takeaways for Miners

#### 1. **Balance is CRITICAL** 🎯
- Aim for 50/50 token ratio at all times

#### 2. **Capital Preservation Matters** 💰
- Minimize impermanent loss

#### 3. **Fees are Secondary** 💵
- Fees only provide 10% weight in score

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

## Resources

- **ARCHITECTURE.md** - System architecture overview
- **JOBS_ARCHITECTURE.md** - Detailed jobs system
- **Protocol Models** - `protocol/models.py` and `protocol/synapses.py`
- **Backtester** - `validator/backtester.py` to understand scoring
- **Database** - `validator/database.py` for historical data access


**Good luck and happy mining! 🚀**

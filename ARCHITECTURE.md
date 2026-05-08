# SN98 ForeverMoney - Architecture Documentation

## System Architecture Overview

SN98 is a decentralized Automated Liquidity Manager (ALM) built on Bittensor. The system optimizes Uniswap V3 / Aerodrome liquidity provision through competitive strategy proposals from miners using a **jobs-based architecture** with **dual-mode operation** (evaluation + live) running concurrently.

## High-Level Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                         Bittensor Network                        │
│  ┌──────────────┐              ┌──────────────────────────────┐  │
│  │   Miners     │◄────────────►│       Validator              │  │
│  │  (N nodes)   │              │    (Jobs Orchestrator)       │  │
│  └──────────────┘              └──────────────────────────────┘  │
│       │  ▲                                 │                     │
│       │  │ VaultRegistrationQuery          │                     │
│       │  └─ RebalanceQuery (eval + live)   │                     │
│       └────────────────────────────────────┘                     │
└──────────────────────────────────────────────────────────────────┘
                                              │
        ┌─────────────────────┬───────────────┼─────────────────────────┐
        │                     │               │                         │
┌───────▼─────────┐ ┌─────────▼───────┐ ┌─────▼────────────┐ ┌──────────▼─────┐
│ Jobs Database   │ │ Pool Events DB  │ │  Executor Bot    │ │ Blockchain RPC │
│ (Tortoise ORM)  │ │  (Read-only)    │ │  (HTTP service)  │ │   (Base L2)    │
│                 │ │                 │ │                  │ │                │
│ • jobs          │ │ • swaps         │ │ /execute_strategy│ │ • Current state│
│ • rounds        │ │ • mints         │ │   → Safe mint/   │ │ • Price feeds  │
│ • predictions   │ │ • burns         │ │     burn         │ │ • Receipts     │
│ • miner_scores  │ │ • collects      │ │ /execute_swap    │ │                │
│ • participations│ │                 │ │   → Safe AK swap │ │                │
│ • live_         │ │                 │ │                  │ │                │
│   executions    │ │                 │ │                  │ │                │
│ • fee_snapshots │ │                 │ │                  │ │                │
└─────────────────┘ └─────────────────┘ └──────────────────┘ └────────────────┘
```

The **Executor Bot** is a separate HTTP service that holds signing keys for each vault's Gnosis Safe. The validator never signs Base L2 transactions directly; it sends JSON payloads to the executor, which builds and submits Safe multisends. Two endpoints today: `/execute_strategy` (mint/burn LP positions) and `/execute_swap` (`SnLiquidityManager.swap()` wrapped in a Safe multisend).

## Jobs-Based Architecture

### Core Concept: Jobs

A **Job** represents a liquidity management task for a specific vault and trading pair. Multiple jobs run **concurrently** in the validator.

```python
Job {
    job_id: str                           # Unique identifier
    sn_liquidity_manager_address: str     # Vault managing liquidity
    pair_address: str                     # Pool address (e.g., WETH/USDC)
    target: str                           # Target objective (currently "PoL")
    chain_id: int                         # 8453 for Base
    round_duration_seconds: int           # Backtest replay length
    is_active: bool                       # Job enabled/disabled
    is_staked: bool                       # If true, executor bot is told
                                          # has_staking_enabled=True so the AK
                                          # is auto-staked after execution.
                                          # Toggled live by service/vault_monitor.
}
```

### Parallel Job Execution

The validator uses **asyncio** to run all active jobs concurrently. Each job runs `run_job_continuously` (`validator/round_orchestrator.py`):

```
┌──────────────────────────────────────────────────────────┐
│              Validator Main Loop (Async)                 │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  Job 1 (ETH/USDC)    Job 2 (WBTC/USDC)   Job 3 (xTAO)    │
│       │                    │                   │         │
│       ├─ Vault filter      ├─ Vault filter     ├─ filter │
│       │  (eligible miners) │                   │         │
│       │                    │                   │         │
│       ├─ Eval Round        ├─ Eval Round       ├─ Eval   │
│       │  (all eligible)    │                   │         │
│       │                    │                   │         │
│       ├─ Live Round        ├─ Live Round       ├─ Live   │
│       │  (winner only)     │                   │         │
│       │                    │                   │         │
│       ├─ sleep 4h ──────▶  │  sleep 4h ───▶    │  sleep  │
│       ▼                    ▼                   ▼         │
└──────────────────────────────────────────────────────────┘
```

The 4-hour idle is a recent change to reduce RPC pressure (`run_job_continuously` calls `asyncio.sleep(4*60*60)` between cycles).

### Dual-Mode Operation

Each job cycle runs both round types in `asyncio.gather`:

#### 1. Evaluation Mode (all eligible miners)
```
Purpose:      Score miner strategies in a forward backtest
Participants: All miners that pass the vault-eligibility filter
              (i.e., miners whose VaultRegistrationQuery returned a
              recognised on-chain SnLiquidityManager).
Replay:       Forward simulation from current chainhead, replaying
              real swap events from base_poocl_swaps_v2.
Cadence:      Strategy queried every `rebalance_check_interval` blocks
              (default 100; configurable).
EMA:          new_eval = 0.9 * old + 0.1 * latest
```

#### 2. Live Mode (winner only)
```
Purpose:      Execute the winner's positions on-chain via the executor.
Participants: The eval winner — IF they have ≥ MINER_ELIGIBILITY_DAYS of
              consistent participation. Default 7 days, prod currently 1.
Dispatch:     POST /execute_strategy on the executor bot with the
              winner's `desired_positions`, `has_staking_enabled` flag,
              and metadata.
Failure:      Non-200 from executor or on-chain revert ⇒ score = -100.
EMA:          new_live = 0.7 * old + 0.3 * latest
```

**Combined Score (used for ranking):**
```python
combined_score = (eval_score * 0.6) + (live_score * 0.4)
```

## Rebalance-Only Protocol

The validator uses a **rebalance-only protocol** with two synapses:

### `VaultRegistrationQuery`
Sent once per discovery pass to any miner not yet present in the validator's vault registry. Miner must answer:
```python
VaultRegistrationQuery {
    has_vault: bool          # True if miner manages an SnLiquidityManager
    vault_address: str       # Address on `chain_id`
    chain_id: int            # 8453 for Base
}
```
Miners that answer `has_vault=False` (or fail to answer) are excluded from all subsequent `RebalanceQuery` rounds for that job.

### `RebalanceQuery`
Sent at every `rebalance_check_interval` block of the eval/live forward replay:
```python
# Request fields (sent by validator)
RebalanceQuery {
    job_id: str
    sn_liquidity_manager_address: str
    pair_address: str
    chain_id: int
    round_id: str
    round_type: str                   # 'evaluation' or 'live'
    block_number: int
    current_price: float
    current_positions: List[Position]
    inventory_remaining: Optional[dict]   # {"amount0": str, "amount1": str}
    rebalances_so_far: int
    tick_spacing: int                     # for snapping desired ticks
}
```
3. **Miners populate response fields on the same synapse:**
   ```python
   # Response fields (populated by miner)
   RebalanceQuery {
       accepted: bool                        # Accept/refuse job
       refusal_reason: Optional[str]         # Why refused (if accepted=False)
       desired_positions: List[Position]     # Desired positions (required if accepted=True)
       miner_metadata: MinerMetadata         # Miner version and model info
   }
   ```
4. **Validator updates simulation** based on miner decisions:
   - If `accepted=False`: Skip miner for entire round
   - If `desired_positions == current_positions`: No rebalance (keep current)
   - If `desired_positions != current_positions`: Rebalance to new positions

### Miner Flexibility

Miners can:
- **Refuse jobs** they don't want to work on (`accepted=False`)
- **Keep current positions** (return `current_positions` as `desired_positions`)
- **Rebalance to new positions** (return new positions as `desired_positions`)
- **Specialize** in certain pairs/strategies per job

## Round Flow Diagram

### Evaluation Round
```
┌────────────────────────────────────────────────────────────┐
│ Start Round (t=0)                                          │
├────────────────────────────────────────────────────────────┤
│                                                            │
│ 1. Get current chainhead (latest block)                    │
│ 2. Get current on-chain positions for the vault            │
│                                                            │
│ ┌────────────────────────────────────────────────────────┐ │
│ │ For Each Miner (Parallel):                             │ │
│ │                                                        │ │
│ │   ┌──────────────────────────────────────────┐         │ │
│ │   │ Forward Simulation (15min duration)      │         │ │
│ │   │                                          │         │ │
│ │   │  Starting from: chainhead state          │         │ │
│ │   │    │                                     │         │ │
│ │   │    ├─ Checkpoint (periodic intervals)    │         │ │
│ │   │    │   ├─ Query miner (RebalanceQuery)   │         │ │
│ │   │    │   ├─ Wait for response (60s max)    │         │ │
│ │   │    │   │                                 │         │ │
│ │   │    │   ├─ If refused: skip miner         │         │ │
│ │   │    │   ├─ If rebalance: update positions │         │ │
│ │   │    │   └─ Else: continue                 │         │ │
│ │   │    │                                     │         │ │
│ │   │    └─ Track performance metrics          │         │ │
│ │   │       (expected PnL, fees, IL, etc.)     │         │ │
│ │   │                                          │         │ │
│ │   └──────────────────────────────────────────┘         │ │ 
│ │                                                        │ │
│ └────────────────────────────────────────────────────────┘ │
│                                                            │
│ 4. Score all miners (rank by expected performance)         │
│ 5. Select winner (best strategy)                           │
│ 6. Update miner scores (EMA: 0.9×old + 0.1×new)            │
│ 7. Update participation tracking                           │
│ 8. Store results in database                               │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

### Live Round
```
┌───────────────────────────────────────────────────────────┐
│ Start Round (t=0)                                         │
├───────────────────────────────────────────────────────────┤
│                                                           │
│ 1. Get previous evaluation winner                         │
│ 2. Check eligibility:                                     │
│    - 7+ days participation?                               │
│                                                           │
│ 3. If not eligible → Skip live round                      │
│ 4. Periodically (every 150 blocks) ask for rebalancing.   │
│ 6. Send rebalance decisions to executor bot               │
│ 7. Track on-chain execution                               │
│ 8. Update live_score (EMA: 0.7×old + 0.3×new)             │
│ 9. Recalculate combined_score                             │
└───────────────────────────────────────────────────────────┘
```

## Database Architecture

### Two-Database Design

SN98 uses **two separate databases**:

#### 1. Jobs Database (Read/Write - Tortoise ORM)
```
Purpose:  Validator state, rounds, scores
ORM:      Tortoise ORM (async)
Tables:
  - jobs               # Active liquidity tasks
  - rounds             # Evaluation/live rounds
  - predictions        # Miner decisions per round
  - miner_scores       # Reputation per job
  - miner_participation # Daily participation
  - live_executions    # On-chain records
```

#### 2. Pool Events Database
```
Purpose:  Historical on-chain events for backtesting
Source:   Subgraph indexer (external)
Tables:
  - swaps     # Swap events with price/tick
  - mints     # Liquidity additions
  - burns     # Liquidity removals
  - collects  # Fee collections
```

**Connection Management:**
```python
# Jobs database
await init_db(JOBS_DB_URL)

# Pool events database
await init_pool_events_db(POOL_EVENTS_DB_URL)

# Both use async Tortoise ORM
# All queries use async/await
```

## Key Features

### 1. Concurrent Execution
- Multiple jobs run simultaneously
- Each job runs eval + live rounds concurrently
- All database operations are async (Tortoise ORM)
- Miner queries parallelized within each round

### 2. Reputation System
- **Per-job scoring** (not global)
- **Historical tracking** using exponential moving averages
- **Weighted scoring** (evaluation vs live)
- **Participation requirements** for live eligibility

### 3. Graceful Degradation
- Miners can refuse jobs without penalty
- Timeout handling for slow/unresponsive miners
- Continue with remaining miners if some fail
- Skip live rounds if winner unavailable

### 4. Flexibility
- Miners choose which jobs to work on
- Dynamic rebalancing during simulation
- Multiple strategies can coexist
- Job-specific specialization

## Example Scenarios

### Scenario 1: New Miner Joins Network

```
Day 1:  Miner participates in evaluation rounds for all jobs
        → Builds evaluation_score across multiple jobs

Day 7:  After 7 days of consistent participation for ETH/USDC job
        → Becomes eligible for live mode

Day 8:  Wins evaluation round for ETH/USDC job
        → Selected for next live round
        → Strategy executed on-chain
        → live_score starts building

Week 2: Continues building reputation
        → combined_score = (0.6 × eval) + (0.4 × live)
        → Competes based on historical performance
```

### Scenario 2: Multiple Jobs Running

```
Validator manages:
  - Job 1: ETH/USDC   (0x123...)
  - Job 2: WBTC/USDC  (0x456...)
  - Job 3: AERO/ETH   (0x789...)

All run concurrently:
  ├─ ETH/USDC    → Eval round + Live round (every 15min)
  ├─ WBTC/USDC   → Eval round + Live round (every 15min)
  └─ AERO/ETH    → Eval round + Live round (every 15min)

Miner A excels at ETH pairs → high score on Jobs 1 & 3
Miner B excels at BTC pairs → high score on Job 2
```

### Scenario 3: Miner Refuses Job

```
1. Validator queries Miner A for Job 2 (WBTC/USDC)

2. Miner A responds:
   RebalanceQuery {
     accepted: false,
     refusal_reason: "Only working on ETH pairs",
     desired_positions: [],  # Empty list when refusing
     miner_metadata: { version: "1.0.0", model_info: "..." }
   }

3. Validator:
   - Logs refusal
   - Skips Miner A for entire round
   - Continues with other miners
   - No penalty to Miner A's scores for other jobs

4. Miner A continues participating in ETH/USDC job
```
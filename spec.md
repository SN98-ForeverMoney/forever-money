# SN98 MVP Code Specification

> ⚠️ **STATUS: SUPERSEDED / LEGACY MVP DESIGN.**
>
> This document captures the original MVP plan: a Flask `/predict_strategy` HTTP endpoint, dual `inventory|position` Mode workflow, `0.7 × Performance + 0.3 × LP Alignment` scoring, etc. **None of this matches live code.**
>
> Live system today: bittensor synapse axon (`RebalanceQuery`, `VaultRegistrationQuery`), exponential-IL-penalty scorer, jobs orchestrator, executor bot. See [`MINER_GUIDE.md`](./MINER_GUIDE.md) and [`ARCHITECTURE.md`](./ARCHITECTURE.md).
>
> Kept for historical context. Do not implement against this spec.

---

This is a comprehensive set of decisions that significantly streamlines the MVP development path for SN98. The choices made clarify the execution environment, data reliance, and scoring rules, allowing for precise specification generation.

Based on the sources and the new team decisions, here is the complete technical specification for the Validator code and a sample implementation for a Miner, ready for your development team.

---

# SN98 MVP Code Specification (legacy)

## Part 1: Validator Code Specification

The Validator is responsible for publishing round parameters, polling miners, enforcing constraints, scoring submissions, and publishing the winning strategy. The core functions rely heavily on the defined data models and the specific scoring mechanics.

### 1. Data and Pricing Decisions (Based on Team Notes)

| Decision | Implementation Requirement | Source(s) |
| :--- | :--- | :--- |
| **PnL vs HODL Price Feed** | **Rely on the subgraph for MVP.** The backtester must query the provided read-only Postgres instance (fed by the subgraph) to derive the asset price used for HODL comparison and performance measurement. An external Oracle is deferred. |
| **Aerodrome Rewards** | **Do not factor in Aerodrome rewards in PnL.** The scoring logic must strictly focus on the performance metrics defined: **Net PnL vs HODL (70%)** and **LP Fee Share of Miners (30%)**. External farming rewards are excluded from the competition score. |
| **Dramatic Slippage / Failed Swaps** | **Validator scoring handles this implicitly.** The Validator's `Backtester` component simulates performance using historical data (from the Postgres DB). If a miner's suggested range leads to dramatic IL or missed fee opportunities, this will result in a poor **Net PnL vs HODL** score. The **Validator is not directly responsible for handling on-chain execution failures (like slippage)**; that is the responsibility of the **Executor Bot (Subnet Owner)** during deployment. |

### 2. Validator Request Generation (Input to Miners)

The Validator must generate a request conforming to the JSON API Format.

| Field | Value/Source | Notes |
| :--- | :--- | :--- |
| `pairAddress` | Current Aerodrome LP Pair | Specifies the token pair for which the strategy is required. |
| `chainId` | `8453` (Base) | Reflects the confirmed initial deployment chain (Aerodrome on Base). |
| `target_block` | Future Block Number | The specific block for which the miner must return a configuration. |
| `mode` | `inventory` (default MVP) | Determines if the miner is deploying new liquidity or adjusting existing positions. |
| `inventory` / `current_positions` | Retrieved from the current vault state (EOA/PoC Vault). | Provides the assets the miner must manage. |
| `metadata.round_id` | Unique ID | Used for tracking the competition round. |
| `metadata.constraints` | Defined by Validator | Must include mandatory constraints like `max_il` (e.g., 0.10), `min_tick_width` (e.g., 60), and `max_rebalances` (e.g., 4). Strategies failing these constraints score zero. |
| **Postgres Access** | **Public or Lightly Authenticated Credentials** | Details (URL/credentials) must be provided securely to grant access to the **read-only public Postgres database** containing pool events. |

### 3. Validator Scoring Implementation

The Validator must use a dedicated scoring function incorporating the 70%/30% weighted criteria.

| Scoring Component | Implementation Detail |
| :--- | :--- |
| **Constraint Check (Pre-Scoring)** | Before evaluating performance, the Validator must verify that the submitted **tick ranges, allocation sizes, and rebalance rules** comply with the `metadata.constraints` (max IL, tick width, rebalance limits). Non-compliant strategies receive a score of 0. |
| **Performance Score (70%)** | Requires a **`Backtester` Class** that simulates the Miner's proposed strategy against the HODL baseline using price data sourced from the subgraph via the Postgres DB. The score is determined by the **Net PnL vs HODL**. |
| **Top-Heavy Weighting** | **Crucial Implementation Detail:** After calculating the PnL performance for all valid strategies, only the **Top 3 strategies receive full weight** (1.0) for this 70% component. All other strategies receive reduced or zero weight based on their rank (scoring may be adjusted as needed). |
| **LP Alignment Score (30%)** | Measures the fees generated from the Miner’s own liquidity vaults. This is scored **pro-rata** based on the Miner's verified contribution to the total LP fees generated across the competing period. |
| **Final Score Calculation** | **Final Score = (Performance Score $\times$ 0.7) + (LP Alignment Score $\times$ 0.3)**. |

### 4. Selection and Execution Mandate

The Validator's primary output is the winning strategy, which mandates the Executor Bot's action.

*   The Validator must **rank miners** based on the Final Score.
*   The Validator publishes the **winning strategy** (the one with the highest Final Score).
*   The **Subnet Owner (Executor Bot)** then reads this strategy and converts it into **v3 NFT LP operations** for deployment on the Aerodrome Vault (PoC EOA or future Safe module contract).

---

## Part 2: Sample Miner Implementation

The Miner's function is purely encapsulated in its prediction endpoint. This sample implementation focuses on structuring the input processing and output formatting, assuming internal logic (ML/rule-based) for generating the optimal `positions` array.

### Miner Requirements Checklist:

1.  Hosts a queryable HTTP endpoint.
2.  Accepts the standardized **Request Format** (JSON).
3.  Utilizes data from the provided Postgres database for strategy calculation.
4.  Returns a valid **Response Format** (JSON) containing `strategy` (positions, rebalance rule) and `miner_metadata`.

### Python Code Example (Conceptual Flask Miner Endpoint)

This example uses Python and Flask to demonstrate the structure of the Miner endpoint.

```python
import json
from flask import Flask, request, jsonify

# --- Configuration ---
app = Flask(__name__)
MINER_VERSION = "1.0.0-mvp"
MODEL_INFO = "Simple-Rule-Based-Aero-Tuner"

# --- Internal Database Handler Simulation (Replace with actual Postgres connection) ---
def query_postgres_for_events(pair_address, target_block, db_credentials):
    """
    Simulates querying the read-only Postgres DB for pool events (swaps, fees, etc.).
    The miner uses this data to run backtests (PnL vs HODL) and decide on ranges.
    """
    print(f"Querying DB for pair {pair_address} up to block {target_block}...")
    # In a real implementation, this would establish a connection using provided credentials
    # and execute SQL to fetch time-series data.
    return {"data_status": "data_retrieved", "events_count": 5000}

# --- Strategy Generation Logic ---
def generate_optimal_strategy(round_data, historical_events):
    """
    This is the core logic. In a real Miner, this function would contain ML models,
    risk management (IL caps), and optimization routines to find the best ranges.
    
    It must ensure the output strategy complies with round_data['metadata']['constraints'].
    """
    constraints = round_data['metadata']['constraints']
    
    # Example: Simple Strategy based on current inventory
    inventory = round_data.get('inventory', {})
    amount0 = int(inventory.get('amount0', 0))
    amount1 = int(inventory.get('amount1', 0))
    
    # 1. Define Position Ranges (Must comply with min_tick_width)
    positions = []
    if amount0 > 0 or amount1 > 0:
        # Example position that meets the minimum tick width constraint (e.g., 60)
        positions.append({
            "tickLower": -9600,
            "tickUpper": -8400, # Tick width is 1200, which is > 60
            "allocation0": str(amount0 // 2),
            "allocation1": str(amount1 // 2),
            "confidence": 0.90 
        })
        positions.append({
            "tickLower": -8400,
            "tickUpper": -7200,
            "allocation0": str(amount0 // 2),
            "allocation1": str(amount1 // 2),
            "confidence": 0.85 
        })

    # 2. Define Optional Rebalance Rule (Must comply with max_rebalances)
    rebalance_rule = {
        "trigger": "price_outside_range",
        "cooldown_blocks": 300 # Complies with constraints
    }

    # IMPORTANT: The Miner must internally ensure this strategy minimizes IL and maximizes fees,
    # as the Validator will score it based on performance.
    
    return {
        "positions": positions,
        "rebalance_rule": rebalance_rule
    }

# --- Miner HTTP Endpoint ---
@app.route('/predict_strategy', methods=['POST'])
def predict_strategy():
    try:
        round_data = request.json
        if not round_data:
            return jsonify({"error": "Invalid JSON input"}), 400

        # 1. Extract inputs
        pair_address = round_data['pairAddress']
        target_block = round_data['target_block']
        
        # Assume Postgres credentials or connection details are included in metadata
        # or accessed via environment variables known to the miner.
        db_credentials = round_data.get('postgres_access', {}) 

        # 2. Query necessary historical data (Miner relies on provided Postgres DB)
        historical_events = query_postgres_for_events(pair_address, target_block, db_credentials)
        
        # 3. Generate the strategy using internal models
        strategy_output = generate_optimal_strategy(round_data, historical_events)
        
        # 4. Construct the final JSON response
        response_json = {
            "strategy": strategy_output,
            "miner_metadata": {
                "version": MINER_VERSION,
                "model_info": MODEL_INFO
            }
        }
        
        return jsonify(response_json), 200

    except KeyError as e:
        return jsonify({"error": f"Missing required field: {e}"}), 400
    except Exception as e:
        return jsonify({"error": f"Internal Miner Error: {e}"}), 500

if __name__ == '__main__':
    # Miners must ensure high availability for polling
    app.run(debug=True, host='0.0.0.0', port=8000)

```

***

### Analogy to Solidify Understanding

The shift to using the subgraph for PnL price means the SN98 system is initially operating like a chef relying solely on the kitchen's internal temperature gauge (the subgraph/Postgres data) to ensure food safety, rather than having an external certified thermometer (an Oracle). This keeps the MVP contained, reliable, and focused entirely on the data it controls, even if it means sacrificing future absolute external price verification.
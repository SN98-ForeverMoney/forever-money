# minarch

**Minimal Architecture for Liquidity Strategy Testing**

minarch is a lightweight, modular framework designed to develop, test, and compare liquidity provision strategies (miners) in concentrated liquidity pools. It provides miners with market data, inventory, and simulation orchestration, along with scoring relative to a HODL baseline.

This is not a production system — it’s a sandbox for experimentation.

## 1. Purpose

- Enable rapid experimentation with multiple miner strategies
- Provide reproducible simulations on historical or synthetic market data
- Evaluate strategies relative to a fixed benchmark (HODL)
- Support multi-position and volatility-aware LP strategies

---

## 2. Folder structure

```
    minarch_lab/
    │
    ├── data/ # Market data
    │ ├── base_poocl_swaps_v2.json # Example market price series
    │ └── data.py # Data loading and utility functions
    │
    ├── miners/ # Implemented miner strategies
    │ ├── baseline_position.py # simple allocation strategy
    │ ├── multi_position.py # 70/30 wide-tight allocation strategy
    │ ├── volatility_band_miner.py # Adaptive volatility-band strategy
    │ └── volatility_miner.py # Volatility-based LP strategy
    │
    ├── services/ # Supporting infrastructure
    │ ├── backtester.py # Runs the backtesting, evaluates position performance
    │ └── scorer.py # Computes HODL-relative scores
    │
    ├── main.py # Example entry point
    └── README.md # This file
```

---

## 3. Strategies Overview

| Strategy             | Width Logic | Rebalance Logic | Positions | Purpose                        |
| -------------------- | ----------- | --------------- | --------- | ------------------------------ |
| **Baseline**         | Fixed       | Edge buffer     | 1         | Deterministic benchmark        |
| **Volatility Aware** | Volatility  | Edge buffer     | 1         | Adaptive coverage              |
| **Volatility Band**  | Volatility  | Center drift    | 1         | Market aware rebalance & width |
| **Multi Positions**  | Volatility  | Edge buffer     | 2 (70/30) | Coverage + concentrated fees   |

---

## 4. Supporting Infrastructure

The framework provides a **minimal, extracted environment** for testing miners:

### 4.1 Market Data (`data/`)

- Extracted from full protocol market feeds for deterministic simulations
- Miners use this to calculate volatility, width, and rebalance triggers

### 4.2 Backtester (`services/backtester.py`)

- Steps through each tick of the market data
- Records positions and auxiliary metrics
- Ensures reproducible, fair evaluation

### 4.3 Scorer (`services/scorer.py`)

- Compares final strategy value to initial inventory (HODL)
- Returns bounded score (e.g., -10 to 10)
- Focused on pure strategy performance, stripped of production complexities

---

## 5. Logging

- Every rebalance and calculation is logged
- Includes volatility, tick ranges, rebalance triggers, and allocations

---

## 6. Run

Install dependencies:

```bash
pip install -r requirements.txt
```

Update main.py with a chosen strategy:

```python
# Volatility-aware single-position strategy
from minarch.miners.volatility_miner import MinimalMiner

# Volatility-band adaptive strategy
from minarch.miners.volatility_band_miner import MinimalMiner

# Multi-position 70/30 strategy
from minarch.miners.multi_positions_miner import MinimalMiner
```

Update main.py with a choosen pair, blocks and chunks size:

```python
block_chunk = 10_000
# eth/usdc pool
pair_address = "0xb2cc224c1c9fee385f8ad6a55b4d94e92359dc59"
# around 1 months of block data
start_block = 41925600
end_block = 43221600
```

Run main.py

```bash
python3 -m minarch.main
```

This will generate an `output.log` file containing all the rebalances, positions, and metrics.

# SN98 ForeverMoney

**Decentralized Automated Liquidity Management on Bittensor**

## Quick Summary

SN98 ForeverMoney is a Bittensor subnet that optimizes Uniswap V3 / Aerodrome liquidity provision through competitive AI strategies. Miners propose dynamic rebalancing decisions, validators evaluate performance through forward simulations, and winning strategies get executed on-chain on Base L2.

**Key Features:**
- **Jobs-Based Architecture** - Multiple liquidity pools managed concurrently
- **Dual-Mode Operation** - Evaluation rounds (all miners) + Live rounds (winners only)
- **Rebalance-Only Protocol** - Miners decide when and how to adjust positions
- **Per-Job Reputation** - Miners build scores for specific trading pairs
- **Participation Requirement** - Consistent performance needed for live execution (default: 7 days)

## Network Information

- **Subnet ID**: 374 (Testnet) / 98 (Mainnet)
- **Network**: Bittensor
- **Protocol**: Uniswap V3 / Aerodrome
- **Round Duration**: Configurable (e.g., 15 minutes)
- **Live Eligibility**: Configurable (default: 7 days participation)

## How It Works

Validators run multiple jobs (liquidity management tasks) concurrently. For each job:

1. **Evaluation Rounds** - All miners compete in forward simulations from current blockchain state
2. **Live Rounds** - Winning miners (after eligible participation period) execute strategies on-chain
3. **Scoring** - Miners scored on absolute inventory protection and value growth
4. **Reputation** - Build per-job scores through exponential moving averages

**Current Scoring (PoL Target):**
- Maximize value growth from pool price appreciation and fees (primary signal)
- Smooth exponential penalty for losing inventory (% of tokens lost)
- Score = value_gain × exp(-10 × loss%) if gaining, value_gain / exp(-10 × loss%) if losing
- 10% inventory loss → 63% score reduction; 50% loss → 99% reduction

## 🚀 Getting Started

Follow these steps to set up your environment and run a miner or validator.

### 1. Prerequisites

- **Python 3.10+**
- **Git**

### 2. Installation

Clone the repository and set up the virtual environment:

```bash
# Clone the repository
git clone https://github.com/SN98-ForeverMoney/forever-money.git
cd forever-money

# Create a virtual environment
python3 -m venv .venv

# Activate the virtual environment
# On Linux/macOS:
source .venv/bin/activate
# On Windows:
# .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configuration

Create a `.env` file from the example:

```bash
cp .env.example .env
```

Edit the `.env` file to match your network configuration (e.g., `NETUID`, `SUBTENSOR_NETWORK`).

---

## ⛏️ Running a Miner

**Getting Started:** Implement a `rebalance_query_handler` that responds to `RebalanceQuery` requests from validators. Accept/refuse jobs and return desired positions (rebalance or keep current). Build reputation through consistent participation for 7 days to become eligible for live execution.

1.  **Register your miner** (if not already registered):
    See [MINER_REGISTRATION_GUIDE.md](./MINER_REGISTRATION_GUIDE.md) for detailed instructions.

2.  **Run the miner**:
    ```bash
    # Using python directly
    python -m miner.miner \
        --wallet.name <your_wallet> \
        --wallet.hotkey <your_hotkey> \
        --netuid 98

    # Using PM2 (Recommended for production)
    pm2 start miner/miner.py --name sn98-miner -- \
        --wallet.name <your_wallet> \
        --wallet.hotkey <your_hotkey> \
        --netuid 98
    ```

For a complete implementation guide and scoring details, see **[MINER_GUIDE.md](./MINER_GUIDE.md)**.

---

## 🛡️ Running a Validator

Validators evaluate miner strategies and execute winning strategies on-chain.

1.  **Database Setup**:
    Validators require a PostgreSQL database to store job history and scores. Ensure you have PostgreSQL installed and configured, then update your `.env` file with the credentials (`JOBS_POSTGRES_*`).

2.  **Run the validator**:
    ```bash
    # Using python directly
    python validator/validator.py \
        --wallet.name <your_wallet> \
        --wallet.hotkey <your_hotkey> \
        --netuid 98

    # Using PM2 (Recommended for production)
    pm2 start validator/validator.py --name sn98-validator -- \
        --wallet.name <your_wallet> \
        --wallet.hotkey <your_hotkey> \
        --netuid 98
    ```

For detailed system architecture, see **[ARCHITECTURE.md](./ARCHITECTURE.md)**.

---

## Documentation

### Core Documentation
- **[MINER_REGISTRATION_GUIDE.md](./MINER_REGISTRATION_GUIDE.md)** - Step-by-step guide to registering a miner on the testnet.
- **[MINER_GUIDE.md](./MINER_GUIDE.md)** - Comprehensive miner implementation guide with scoring details.
- **[ARCHITECTURE.md](./ARCHITECTURE.md)** - Deep dive into the system architecture, round flows, and database design.

## Contributing

This is an active Bittensor subnet. Contributions are welcome:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## Support

- **Issues**: Open a GitHub issue
- **Bittensor Discord**: Join the community
- **Documentation**: Check the docs/ folder

## License

MIT License - see [LICENSE](./LICENSE) file for details

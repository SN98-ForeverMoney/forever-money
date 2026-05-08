# SN98 ForeverMoney

**Decentralized Automated Liquidity Management on Bittensor**

## Quick Summary

SN98 ForeverMoney is a Bittensor subnet that optimizes Uniswap V3 / Aerodrome liquidity provision through competitive AI strategies. Miners propose dynamic rebalancing decisions, validators evaluate performance through forward simulations, and winning strategies get executed on-chain on Base L2.

**Key Features:**
- **Jobs-Based Architecture** — multiple liquidity pools managed concurrently
- **Dual-Mode Operation** — evaluation rounds (all eligible miners) + live rounds (winners only)
- **Rebalance-Only Protocol** — miners propose `desired_positions` per pool
- **Per-Job Reputation** — EMA-weighted scores per trading pair
- **Participation Gate** — `MINER_ELIGIBILITY_DAYS` of consistent eval performance before live execution (default 7; production currently runs at 1)

## Network Information

- **Subnet ID**: 98 (mainnet) / 374 (testnet)
- **Network**: Bittensor
- **Underlying protocol**: Uniswap V3 / Aerodrome on Base (chain `8453`)
- **Round cadence**: one evaluation+live round pair per job, then **4-hour sleep** before the next pair (`validator/round_orchestrator.py:run_job_continuously`)

## How It Works

Validators run multiple jobs concurrently. Per job:

1. **Vault filtering** — only miners that report a registered on-chain `SnLiquidityManager` via `VaultRegistrationQuery` are eligible.
2. **Evaluation round** — eligible miners receive `RebalanceQuery` over a forward backtest from current chainhead. Strategies are scored on simulated outcome.
3. **Live round** — for eligibility-cleared miners, the winner's positions are dispatched to the executor bot (`/execute_strategy`) and minted on-chain via the vault's Safe.
4. **Score update** — bounded relative-return score with IL penalty and in-range bonus, smoothed via EMA.

**Scoring summary (PoL target — see [MINER_GUIDE.md](./MINER_GUIDE.md) for full math):**

```
return_pct = clamp((final - initial) / initial, -10, +10)
penalty    = exp(-10 * impermanent_loss)             # IL ratio, not %
score      = (return_pct * penalty) if return_pct >= 0 else (return_pct / penalty)
score     *= 0.92 + 0.08 * in_range_ratio            # small in-range bonus
score      = clamp(score, -100, +10)                 # JSON-safe bounds
# Failed live execution short-circuits to score = -100.
```

10% IL ⇒ penalty 0.37; 20% IL ⇒ 0.14. Inventory loss is punished symmetrically (gains shrink, losses amplify).

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

**Getting Started:** Implement two synapse handlers — `vault_registration_handler` (returns your `SnLiquidityManager` address; without this you are filtered out and never queried) and `rebalance_query_handler` (returns `desired_positions` for each `RebalanceQuery`). Build reputation through consistent eval-round participation; after `MINER_ELIGIBILITY_DAYS` you become eligible for live execution.

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

3.  **Auto-Update (optional)**  
    By default the validator runs an auto-update task every **1 hour**: it executes `scripts/update_to_latest.sh` to fetch the latest release from the repo, install dependencies, and (if you use PM2) restart processes so new code is loaded.

    - **Enable (default):** `--auto-update true` — runs the update script every 3600 seconds.
    - **Disable:** `--auto-update false` — no automatic updates.

    ```bash
    # Disable auto-update
    python validator/validator.py \
        --wallet.name <your_wallet> \
        --wallet.hotkey <your_hotkey> \
        --netuid 98 \
        --auto-update false
    ```

    You can also run the update script manually from the repo root:
    ```bash
    chmod +x scripts/update_to_latest.sh
    ./scripts/update_to_latest.sh              # update to latest release tag
    ./scripts/update_to_latest.sh main         # update to branch main instead
    ./scripts/update_to_latest.sh --no-restart # skip pm2 restart
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

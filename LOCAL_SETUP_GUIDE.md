# Local Subnet Setup Guide

This guide will help you run the SN98 ForeverMoney subnet locally with your Docker-based subtensor.

## Prerequisites

Before running the subnet locally, you need to set up your local Bittensor blockchain and wallets.

### Step 0: Deploy Local Subtensor Blockchain

If you haven't already, deploy the local Subtensor blockchain:

```bash
# Pull the official subtensor localnet Docker image
docker pull ghcr.io/opentensor/subtensor-localnet:devnet-ready

# Run the localnet (if not already running)
docker run -d \
  --name test_local_chain \
  -p 9944:9944 -p 9945:9945 \
  ghcr.io/opentensor/subtensor-localnet:devnet-ready
```

Verify it's running:

```bash
docker ps | grep subtensor
# Should show: test_local_chain_   Up X hours   0.0.0.0:9944-9945->9944-9945/tcp
```

### Step 1: Create Wallets

You need to create three wallets: one for the subnet owner, one for the validator, and one for the miner.

**Note:** Local blockchains are pre-provisioned with an "Alice" account loaded with 1,000,000 TAO for testing.

First, access the Alice account:

```bash
# Create wallet from Alice account (pre-funded with 1M TAO)
btcli wallet create --uri alice

# Verify Alice's balance
btcli wallet balance --wallet.name alice --network ws://127.0.0.1:9945
```

Now create the three role-specific wallets:

```bash
# 1. Create subnet owner wallet
btcli wallet create \
  --wallet.name sn-creator \
  --hotkey default 
  
# 2. Create validator wallet
btcli wallet create \
  --wallet.name test_validator \
  --hotkey default

# 3. Create miner wallet
btcli wallet create \
  --wallet.name test_miner \
  --hotkey default
```

**Important:** Save your mnemonic seed phrases securely! The mnemonics will be displayed during creation.

### Step 2: Fund the Wallets

Transfer TAO from Alice to each of your new wallets:

```bash
# First, list wallets to get their addresses
btcli wallet list

# Transfer TAO from Alice to subnet creator (needs at least 1000 TAO for subnet creation)
btcli wallet transfer \
  --wallet.name alice \
  --destination <SN_CREATOR_COLDKEY_ADDRESS> \
  --amount 5000 \
  --network ws://127.0.0.1:9945

# Transfer TAO to validator (needs TAO for staking to get validator permit)
btcli wallet transfer \
  --wallet.name alice \
  --destination <VALIDATOR_COLDKEY_ADDRESS> \
  --amount 5000 \
  --network ws://127.0.0.1:9945

# Transfer TAO to miner (needs small amount for registration)
btcli wallet transfer \
  --wallet.name alice \
  --destination <MINER_COLDKEY_ADDRESS> \
  --amount 1000 \
  --network ws://127.0.0.1:9945
```

Verify the balances:

```bash
btcli wallet balance --wallet.name sn-creator --network ws://127.0.0.1:9945
btcli wallet balance --wallet.name test_validator --network ws://127.0.0.1:9945
btcli wallet balance --wallet.name test_miner --network ws://127.0.0.1:9945
```

### Step 3: Create the Subnet

Create your subnet on the local blockchain:

```bash
btcli subnet create \
  --subnet-name sn98-forever-money \
  --wallet.name sn-creator \
  --network ws://127.0.0.1:9945
```

You'll be prompted to confirm the burn cost (τ 1,000.0000 initially) and enter your wallet password.

Verify the subnet was created:

```bash
btcli subnet list --network ws://127.0.0.1:9945
```

Note the **NETUID** assigned to your subnet (typically 2 for the subnet you create, as there already is one).

### Step 4: Start Subnet Emissions

Enable token emissions on your subnet:

```bash
btcli subnet start \
  --netuid 2 \
  --wallet.name sn-creator \
  --network ws://127.0.0.1:9945
```

### Step 5: Register Miner and Validator

Register both the miner and validator hotkeys to your subnet:

```bash
# Register miner hotkey
btcli subnet register \
  --netuid 2 \
  --wallet.name test_miner \
  --hotkey default \
  --network ws://127.0.0.1:9945

# Register validator hotkey
btcli subnet register \
  --netuid 2 \
  --wallet.name test_validator \
  --hotkey default \
  --network ws://127.0.0.1:9945
```

### Step 6: Stake TAO for Validator Permit

Stake sufficient TAO to your validator hotkey to earn a validator permit:

```bash
btcli stake add \
  --netuid 2 \
  --wallet.name test_validator \
  --hotkey default \
  --amount 1000 \
  --unsafe \
  --network ws://127.0.0.1:9945
```

Verify your validator has a permit:

```bash
btcli subnet show --netuid 2 --network ws://127.0.0.1:9945
```

## Current Setup

✅ **After completing the above steps, you should have:**
- Local Subtensor: `test_local_chain` running on ports 9944-9945
- Wallets: `sn-creator`, `test_validator`, `test_miner` created and funded
- Subnet created with netuid 2 and emissions enabled
- Miner and validator hotkeys registered to the subnet
- Validator staked with sufficient TAO for validator permit

## Quick Start Commands
#todo: update this section  

### 1. Environment Setup

First, create your `.env` file:

```bash
# Create .env from template
cp .env.example .env

# Edit the .env file with your local configuration
nano .env
```

**Recommended `.env` for local testing:**

```bash
# Database Configuration (adjust if you have local postgres)
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=sn98_pool_data
POSTGRES_USER=readonly_user
POSTGRES_PASSWORD=your_password

# Or use connection string instead:
# DB_CONNECTION_STRING=postgresql://user:pass@localhost:5432/sn98_pool_data

# Pair Configuration
PAIR_ADDRESS=0x1024c20c048ea6087293f46d4a1c042cb6705924
CHAIN_ID=8453
START_BLOCK=35330091
TARGET_BLOCK=38634763

# Constraints
MAX_IL=0.10
MIN_TICK_WIDTH=60
MAX_REBALANCES=4

# Miner Configuration
MINER_PORT=8000
MINER_VERSION=1.0.0-dev
MODEL_INFO=simple-rule-based
```

### 2. Install Dependencies

```bash
# Make sure you're in the project directory
cd /home/ardian/cb/forever-money

# Activate virtual environment if you have one
source .venv/bin/activate  # or create new: python -m venv .venv

# Install dependencies
pip install -r requirements.txt
```

### 3. Start the Miner

In **Terminal 1**, run the miner using Bittensor axon:

```bash
# Using Bittensor protocol (recommended for local subnet)
python -m miner.miner \
  --wallet.name test_miner \
  --wallet.hotkey default \
  --subtensor.network local \
  --subtensor.chain_endpoint ws://localhost:9944 \
  --netuid 2 \
  --axon.port 8091

# The miner will serve on the axon port (default: 8091)
```

**Alternative: HTTP mode (for testing without Bittensor):**

If your miner still has HTTP endpoints:

```bash
python -m miner.miner
# This would start on port 8000
```

### 4. Run the Validator

In **Terminal 2**, run the validator:

```bash
python -m validator.main \
  --wallet.name test_validator \
  --wallet.hotkey default \
  --subtensor.network local \
  --subtensor.chain_endpoint ws://localhost:9944 \
  --netuid 2 \
  --pair_address 0x1024c20c048ea6087293f46d4a1c042cb6705924 \
  --target_block 38634763 \
  --start_block 35330091 \
  --dry-run
```

**Explanation of flags:**
- `--subtensor.network local` - Use local network
- `--subtensor.chain_endpoint ws://localhost:9944` - Connect to your Docker subtensor
- `--netuid 2` - Use subnet 1 (local subnets typically use 1)
- `--dry-run` - Don't publish weights (for testing)

## Complete Test Flow

Use the integration test script that runs everything:

```bash
# Run full integration test
python scripts/test_integration.py
```

This script will:
1. Start the miner automatically
2. Run the validator with test configuration
3. Verify the results
4. Clean up processes

## Step-by-Step Manual Testing

### Step 1: Verify Subtensor is Running

```bash
# Check Docker container
docker ps | grep subtensor

# Should show:
# test_local_chain_   Up X hours   0.0.0.0:9944-9945->9944-9945/tcp
```

### Step 2: Test Miner Health

If running miner in HTTP mode:

```bash
# In a separate terminal, check miner health
curl http://localhost:8000/health

# Expected response:
# {"status": "healthy", "version": "1.0.0-dev", "model": "simple-rule-based"}
```

### Step 3: Check Miner Registration

After starting the miner, verify it's registered in the metagraph:

```bash
btcli subnet show --netuid 2 --network ws://127.0.0.1:9945

# You should see your miner listed with a UID (e.g., UID 0)
```

### Step 4: Run Validator Against Miner

Now run the validator. You can either:

**Option A: Query all active miners** (default):
```bash
python -m validator.main \
  --wallet.name test_validator \
  --wallet.hotkey default \
  --subtensor.network local \
  --netuid 2 \
  --pair_address 0x1024c20c048ea6087293f46d4a1c042cb6705924 \
  --target_block 38634763 \
  --start_block 35330091 \
  --dry-run
```

**Option B: Query specific miner(s) by UID** (useful for testing):
```bash
# Query only miner with UID 0
python -m validator.main \
  --wallet.name test_validator \
  --wallet.hotkey default \
  --subtensor.network local \
  --netuid 2 \
  --pair_address 0x1024c20c048ea6087293f46d4a1c042cb6705924 \
  --target_block 38634763 \
  --start_block 35330091 \
  --miner-uids 0 \
  --dry-run

# Or query multiple miners:
# --miner-uids 0,1,2
```

### Step 5: Check Results

```bash
# View the winning strategy
cat winning_strategy.json | jq .

# View validator logs
tail -f validator.log
```

## Using the New Inventory System

If you want to test with the LiquidityManager contract inventory:

```bash
# Set up environment variables
export LIQUIDITY_MANAGER_ADDRESS=0x...  # Your contract address
export RPC_URL=https://mainnet.base.org

# Run the example
python examples/inventory_usage.py
```

Or integrate it into the validator:

```python
# In validator/main.py, replace the hardcoded inventory with:
from validator.services.liqmanager import SnLiqManagerService

provider = SnLiqManagerService(
   liquidity_manager_address=os.getenv("LIQUIDITY_MANAGER_ADDRESS"),
   rpc_url=os.getenv("RPC_URL")
)

inventory = provider.get_inventory(
   pair_address=config['pair_address'],
   chain_id=config['chain_id']
)
```

## Troubleshooting

### Miner won't start

```bash
# Check if port is in use
lsof -i :8091

# Kill existing process
kill -9 $(lsof -t -i:8091)

# Check logs
tail -f miner.log
```

### Validator can't connect to subtensor

```bash
# Verify Docker container is running
docker ps | grep subtensor

# Check port is accessible
nc -zv localhost 9944

# Restart Docker container if needed
docker restart test_local_chain_
```

### Database connection errors

If you don't have a local database, you can:

1. **Run without database** (miner will use default values):
   ```bash
   # Don't set DB_CONNECTION_STRING or POSTGRES_* vars
   unset DB_CONNECTION_STRING
   ```

2. **Set up local PostgreSQL** with Docker:
   ```bash
   docker run -d \
     --name sn98-postgres \
     -e POSTGRES_DB=sn98_pool_data \
     -e POSTGRES_USER=readonly_user \
     -e POSTGRES_PASSWORD=testpass \
     -p 5432:5432 \
     postgres:15

   # Then update .env
   POSTGRES_HOST=localhost
   POSTGRES_PASSWORD=testpass
   ```

## Quick Commands Reference

```bash
# Start everything in one go (integration test)
python scripts/test_integration.py

# Or manually:

# Terminal 1: Miner
python -m miner.miner \
  --wallet.name test_miner \
  --wallet.hotkey default \
  --subtensor.network local \
  --netuid 2 \
  --axon.port 8091

# Terminal 2: Check miner registered
btcli subnet show --netuid 2 --network ws://127.0.0.1:9945

# Terminal 3: Validator (query all miners)
python -m validator.main \
  --wallet.name test_validator \
  --wallet.hotkey default \
  --subtensor.network local \
  --netuid 2 \
  --pair_address 0x1024c20c048ea6087293f46d4a1c042cb6705924 \
  --target_block 38634763 \
  --start_block 35330091 \
  --dry-run

# Or query specific miner UID:
python -m validator.main \
  --wallet.name test_validator \
  --wallet.hotkey default \
  --subtensor.network local \
  --netuid 2 \
  --pair_address 0x1024c20c048ea6087293f46d4a1c042cb6705924 \
  --target_block 38634763 \
  --start_block 35330091 \
  --miner-uids 0 \
  --dry-run

# Terminal 4: Check results
cat winning_strategy.json | jq .
```

## Next Steps

1. **Modify the miner strategy** in `miner/strategy.py`
2. **Test with different constraints** by editing `.env`
3. **Run backtests** with different block ranges
4. **Integrate inventory provider** with LiquidityManager contract

## Development Workflow

```bash
# 1. Make changes to miner/strategy.py
nano miner/strategy.py

# 2. Restart miner
# (Ctrl+C in miner terminal, then restart)

# 3. Run validator to test
python -m validator.main --dry-run ...

# 4. Check results
cat winning_strategy.json | jq .

# 5. Repeat!
```

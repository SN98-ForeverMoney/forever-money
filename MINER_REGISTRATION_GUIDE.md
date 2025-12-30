# Miner Registration Guide - Dev Network

This guide will help you get funds and register as a miner on the SN98 ForeverMoney subnet running at **198.7.122.37**.

> **⚠️ IMPORTANT NOTICE**
> This is a development/test network and **may be restarted at any time**.

## Prerequisites

- `btcli` (Bittensor CLI) installed
- Python 3.8+ with required dependencies

## Step 1: Create Your Miner Wallet

Create a new wallet for your miner:

```bash
btcli wallet create \
  --wallet.name my_miner \
  --hotkey default
```

**IMPORTANT:** Save your mnemonic seed phrase securely! You'll need it to recover your wallet.

Get your wallet address:

```bash
btcli wallet list
```

Note your **coldkey address** - you'll need this to receive TAO.

## Step 2: Get TAO Funds

You need TAO to register on the subnet. This dev network has a pre-funded **Alice** account with 1,000,000 TAO for testing.

**Recommended:** 1000 TAO for operations and staking

### Option A: Transfer from Alice Wallet (Recommended for Dev Network)

First, get access to the Alice wallet:

```bash
# Import Alice account (pre-funded with 1M TAO)
btcli wallet create --uri alice

# Verify Alice's balance
btcli wallet balance \
  --wallet.name alice \
  --network ws://198.7.122.37:9945
```

Now transfer TAO from Alice to your miner wallet:

```bash
# Get your miner's coldkey address first
btcli wallet list

# Transfer TAO from Alice to your miner
btcli wallet transfer \
  --wallet.name alice \
  --destination <YOUR_MINER_COLDKEY_ADDRESS> \
  --amount 1000 \
  --network ws://198.7.122.37:9945
```

Replace `<YOUR_MINER_COLDKEY_ADDRESS>` with your coldkey address from `btcli wallet list`.

### Option B: Request from Subnet Owner

Alternatively, contact the subnet owner/administrator and provide them with your **coldkey address** from Step 1.

### Verify Your Balance

Once you have TAO, verify your balance:

```bash
btcli wallet balance \
  --wallet.name my_miner \
  --network ws://198.7.122.37:9945
```

## Step 3: Verify Subnet Information

The SN98 ForeverMoney subnet is configured as:
- **Subnet Name**: `sn98-forever-money`
- **NETUID**: `2`

You can verify this by listing all subnets:

```bash
btcli subnet list --network ws://198.7.122.37:9945
```

## Step 4: Register Your Miner

Register your miner hotkey to the subnet:

```bash
btcli subnet register \
  --netuid 2 \
  --wallet.name my_miner \
  --hotkey default \
  --network ws://198.7.122.37:9945
```

You'll be prompted to:
1. Confirm the registration cost (burn)
2. Enter your wallet password

**Note:** Registration requires burning some TAO. The amount depends on network conditions.

## Step 5: Verify Registration

Check that your miner is registered:

```bash
btcli subnet show \
  --netuid 2 \
  --network ws://198.7.122.37:9945
```

You should see your hotkey listed with a UID (User ID).

## Step 6: Set Up Your Miner

### Install Dependencies

```bash
# Clone the repository (if you haven't already)
git clone https://github.com/SN98-ForeverMoney/forever-money.git
cd forever-money

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Configure Environment (Optional)

Create a `.env` file with your configuration:

```bash
# Copy example file
cp .env.example .env

# Edit with your settings
nano .env
```


## Step 7: Run Your Miner

Start your miner using Bittensor axon:

```bash
python -m miner.miner \
  --wallet.name my_miner \
  --wallet.hotkey default \
  --subtensor.network finney \
  --subtensor.chain_endpoint ws://198.7.122.37:9944 \
  --netuid 2 \
  --axon.port 8091
```

**Important flags:**
- `--subtensor.network finney` - Use finney network type
- `--subtensor.chain_endpoint ws://198.7.122.37:9944` - Connect to dev network
- `--netuid 2` - SN98 ForeverMoney subnet ID
- `--axon.port 8091` - Port for receiving validator queries (must be publicly accessible)

### Port Forwarding

**CRITICAL:** Your miner must be accessible from the internet for validators to query you.

Make sure port **8091** (or your chosen axon port) is:
1. Open in your firewall
2. Forwarded in your router (if behind NAT)
3. Accessible from the internet

Test accessibility:
```bash
# From another machine
curl http://YOUR_PUBLIC_IP:8091/health
```

## Step 8: Monitor Your Miner

### Check Logs

```bash
tail -f miner.log
```

Good luck mining! 🚀

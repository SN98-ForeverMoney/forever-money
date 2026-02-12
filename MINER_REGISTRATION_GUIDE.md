# Miner Registration Guide - Testnet

This guide will help you get funds and register as a miner on the SN98 ForeverMoney subnet running on **Bittensor Testnet**.

> **⚠️ IMPORTANT NOTICE**
> This is a test network.

## Prerequisites

- `btcli` (Bittensor CLI) installed
- Python 3.11+ with required dependencies


## 💡 Pro Tip: Set Default Network

To avoid typing `--network test` for every command, you can set it as your default:

```bash
btcli config set --network test
```

Now you can run commands like `btcli wallet list` without the network flag!


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

## Step 2: Get Testnet TAO Funds

You need Testnet TAO to register on the subnet.

You can request funds [here](https://app.minersunion.ai/testnet-faucet)

### Verify Your Balance

Once you have TAO, verify your balance:

```bash
btcli wallet balance \
  --wallet.name my_miner
```

## Step 3: Verify Subnet Information

The SN98 ForeverMoney subnet is configured as:
- **Subnet Name**: `forevermoney`
- **NETUID**: `374`

You can verify this by listing all subnets:

```bash
btcli subnet list
```

## Step 4: Register Your Miner

Register your miner hotkey to the subnet:

```bash
btcli subnet register \
  --netuid 374 \
  --wallet.name my_miner \
  --hotkey default
```

You'll be prompted to:
1. Confirm the registration cost (burn)
2. Enter your wallet password

**Note:** Registration requires burning some TAO. The amount depends on network conditions.

## Step 5: Verify Registration

Check that your miner is registered:

```bash
btcli subnet show \
  --netuid 374
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

Set `SUBTENSOR_NETWORK=test` and `NETUID=374` in your `.env` file.


## Step 7: Run Your Miner

Start your miner using Bittensor axon:

```bash
python -m miner.miner \
  --wallet.name my_miner \
  --wallet.hotkey default \
  --netuid 374 \
  --axon.port 8091
```

**Important flags:**
- `--netuid 374` - SN98 ForeverMoney subnet ID
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

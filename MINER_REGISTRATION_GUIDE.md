# Miner Registration Guide

This guide gets you from zero to a registered, queryable miner on the SN98 **ForeverMoney** subnet — on either testnet or mainnet.

| Network  | NETUID | `btcli` flag      |
|----------|-------:|-------------------|
| mainnet  | **98** | `--network finney` (default) |
| testnet  | **374** | `--network test`  |

The flow is identical on both — substitute your `NETUID` and network. Examples below show **testnet** (`374`); add `--netuid 98` and `--network finney` for mainnet.

> ⚠️ Mainnet registration costs real TAO and your miner will be live-scored. Test on testnet first.

## Prerequisites

- `btcli` (Bittensor CLI) installed
- Python 3.11+
- A deployed `SnLiquidityManager` vault on Base (chain `8453`) that you control. Without one, the validator's vault filter will skip you and you will not be queried. See [Vault Setup](#vault-setup) below.

## 💡 Pro Tip: Set Default Network

To avoid typing `--network` on every command:

```bash
btcli config set --network test     # testnet
# or
btcli config set --network finney   # mainnet
```


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

### Configure Environment

```bash
cp .env.example .env
nano .env
```

Set in `.env`:
- `SUBTENSOR_NETWORK=test` (or `finney` for mainnet)
- `NETUID=374` (or `98` for mainnet)
- `MINER_VAULT_ADDRESSES=["0xYourLiquidityManagerAddress"]` — JSON-encoded list (see Vault Setup below)
- `MINER_VAULT_CHAIN_ID=8453`

## Vault Setup

The validator's vault-eligibility filter (`validator/round_orchestrator.py`) will only query miners that report a registered on-chain vault. Each miner must:

1. Deploy or be granted ownership of an `SnLiquidityManager` contract on Base (chain `8453`).
2. Register at least one Aerodrome/Uniswap V3 pool's AK token to a `PositionManager` on that vault.
3. Stash some token0/token1 in the vault so live execution has inventory to deploy.
4. Set `MINER_VAULT_ADDRESSES` so your miner's `vault_registration_handler` returns `has_vault=True` with the right address.

If you don't yet have a vault, contact the team — vaults are deployed via the ForeverMoney webapp factory.

## Step 7: Run Your Miner

```bash
python -m miner.miner \
  --wallet.name my_miner \
  --wallet.hotkey default \
  --netuid 374 \
  --axon.port 8091
```

**Important flags:**
- `--netuid` — `374` testnet, `98` mainnet
- `--axon.port 8091` — port for receiving validator queries (must be publicly accessible)

### Port Forwarding

**CRITICAL:** Your axon must be reachable from the public internet for validators to query you.

Ensure port `8091` (or your chosen `--axon.port`) is:
1. Open in your firewall
2. Forwarded in your router (if behind NAT)
3. Reachable externally

Bittensor axons do not expose `/health`. Verify reachability via the metagraph instead:

```bash
btcli s metagraph --netuid 374
```

Find your hotkey row — the `axon` column should show a public `<ip>:<port>`. If it shows `0.0.0.0:0`, your `axon.serve` call hasn't propagated yet (wait ~1 epoch) or your IP is unreachable.

## Step 8: Monitor Your Miner

```bash
tail -f miner.log
```

Look for incoming `RebalanceQuery` and `VaultRegistrationQuery` calls.

## Eligibility for Live Execution

You start in **evaluation-only** mode. After `MINER_ELIGIBILITY_DAYS` of consistent participation (env var on the validator; default 7, currently `1` in production), you become eligible for live on-chain execution. Until then, only your eval scores matter — no live payload is dispatched to the executor for your hotkey.

Good luck mining! 🚀

# SN98 ForeverMoney - Complete Deployment Guide

**Complete step-by-step guide from setup to successful validator registration**

**Last Updated:** November 27, 2025
**Status:** ✅ Tested and Working
**Network:** Bittensor Testnet (Subnet 98)

---

## 📋 Table of Contents

2. [Environment Setup](#environment-setup)
3. [Wallet Creation](#wallet-creation)
4. [Getting Testnet TAO](#getting-testnet-tao)
5. [Registration on Subnet 98](#registration-on-subnet-98)
6. [Verification](#verification)
7. [Troubleshooting](#troubleshooting)
8. [Next Steps](#next-steps)

---

## Environment Setup

### Step 1: Clone the Repository

```bash
git clone <repository-url>
cd forever-money
```

### Step 2: Create Virtual Environment

```bash
# Create virtual environment
python3 -m venv venv

# Activate it
source venv/bin/activate  # On macOS/Linux
# OR
venv\Scripts\activate     # On Windows
```

### Step 3: Install Dependencies

```bash
# Upgrade pip first
pip install --upgrade pip

# Install core dependencies
pip install bittensor==9.12.2

# Install PyTorch (required for registration)
pip install torch

# Install other dependencies from requirements.txt if available
pip install -r requirements.txt  # If file exists
```

**Verify Installation:**

```bash
python -c "import bittensor as bt; print(f'Bittensor version: {bt.__version__}')"
# Expected output: Bittensor version: 9.12.2
```

---

## Wallet Creation

### Step 1: Understanding Bittensor Wallets

A Bittensor wallet consists of two keys:

- **Coldkey:** Stores your TAO tokens (like a bank account)
- **Hotkey:** Used for validator operations (like a debit card)

### Step 2: Create Wallet Using Python API

**Option A: Interactive Creation (Recommended for Production)**

```bash
python -c "
import bittensor as bt

wallet = bt.wallet(name='test_validator', hotkey='test_hotkey')

# Create coldkey with password
wallet.create_new_coldkey(use_password=True, overwrite=False)

# Create hotkey (no password needed)
wallet.create_new_hotkey(use_password=False, overwrite=False)

print(f'✅ Wallet created!')
print(f'Coldkey: {wallet.coldkeypub.ss58_address}')
print(f'Hotkey: {wallet.hotkey.ss58_address}')
"
```

**Option B: Quick Creation (For Testing)**

```bash
python -c "
import bittensor as bt

wallet = bt.wallet(name='test_validator', hotkey='test_hotkey')
wallet.create_new_coldkey(use_password=False, overwrite=False)
wallet.create_new_hotkey(use_password=False, overwrite=False)

print(f'Coldkey: {wallet.coldkeypub.ss58_address}')
print(f'Hotkey: {wallet.hotkey.ss58_address}')
"
```

### Step 3: Save Your Wallet Information

**CRITICAL: Save these mnemonics in a secure location (offline preferred)**

The wallet creation will output mnemonics like:

```
Coldkey mnemonic: word1 word2 word3 ... word12
Hotkey mnemonic: word1 word2 word3 ... word12
```

**⚠️ WARNING:** Anyone with these mnemonics can access your wallet!

### Step 4: Verify Wallet Files

```bash
ls -la ~/.bittensor/wallets/test_validator/
# Expected output:
# coldkey (or coldkeypub.txt)
# hotkeys/test_hotkey
```

---

## Getting Testnet TAO

### Understanding Testnet TAO

- Testnet TAO has no real value
- Used for testing on testnet only
- Free to obtain from faucets

### Method 1: Request from Project Founder

Contact your subnet founder and provide your **coldkey address**.

### Method 2: Bittensor Discord Faucet

1. Go to https://app.minersunion.ai/testnet-faucet and get TAO tokens

### Verify Balance

```bash
python -c "
import bittensor as bt

wallet = bt.wallet(name='test_validator', hotkey='test_hotkey')
subtensor = bt.subtensor(network='test')

balance = subtensor.get_balance(wallet.coldkeypub.ss58_address)
print(f'Balance: {balance} TAO')
"
```

**Expected:** At least 0.001 TAO for registration

---

## Registration on Subnet 98

### ⚠️ IMPORTANT: Registration Method Choice

**There are TWO registration methods:**

1. **PoW (Proof-of-Work) Registration** - ❌ HAS A BUG in Bittensor SDK 9.12.2
2. **Burned Registration** - ✅ WORKS PERFECTLY

**Issue with PoW Registration:**

- The Bittensor SDK signs PoW registration with the coldkey
- The chain expects it to be signed with the hotkey
- This causes `TransactorAccountShouldBeHotKey` error
- All PoW attempts will fail with "stale POW" errors

**Solution:** Use burned registration instead (costs ~0.0008 TAO)

---

### Method 1: Burned Registration (RECOMMENDED)

**Cost:** ~0.0008 testnet TAO (minimal)
**Time:** Instant (no proof-of-work needed)
**Success Rate:** 100%

#### Step 1: Create Registration Script

The script `register_burned.py` is already in the repository.

#### Step 2: Run Registration

```bash
source venv/bin/activate
python register_burned.py
```

#### Expected Output:

```
======================================================================
REGISTERING VALIDATOR ON SUBNET 98 (TESTNET)
Using BURNED REGISTRATION (not PoW)
======================================================================

1. Loading wallet...
   ✅ Wallet loaded
      Coldkey: 5D...
      Hotkey: 5H...

2. Connecting to testnet...
   ✅ Connected to testnet

3. Checking balance...
   Coldkey balance: τ0.497029276 TAO

4. Checking if already registered on subnet 98...
   Not registered yet

5. Checking burn cost for subnet 98...
   Min burn: ~0.0005 TAO (subnet 98 setting)
   ✅ Sufficient balance for burned registration

6. Registering on subnet 98 (burned registration)...
   Network: testnet
   Netuid: 98

🎉 REGISTRATION SUCCESSFUL!

✅ Your validator is registered on subnet 98 (testnet)
   Your UID: 240
   Hotkey: 5HnPJfR6uDCXCg4DJNoFA3yBuXDcwhzGGAf2huu9iR23dZ6Q
   TAO burned: 0.000801
   Remaining balance: τ0.496228516 TAO
```

#### Step 3: Save Your UID

**YOUR UID IS CRITICAL** - You'll need it for validator deployment.

Example: `UID: 240`

---

### Method 2: PoW Registration (NOT RECOMMENDED - Has SDK Bug)

**⚠️ DO NOT USE THIS METHOD** - It will fail due to SDK signing bug.

If you try it anyway, you'll see:

```
❌ Failed: Subtensor returned `TransactorAccountShouldBeHotKey(Module)` error
POW is stale.
```

**Why it fails:**

- Chain expects: Transaction signed by hotkey
- SDK actually does: Transaction signed by coldkey
- Result: Transaction rejected every time

---

## Verification

### Verify Registration Success

```bash
python -c "
import bittensor as bt

wallet = bt.wallet(name='test_validator', hotkey='test_hotkey')
subtensor = bt.subtensor(network='test')

metagraph = subtensor.metagraph(netuid=98)
hotkey_ss58 = wallet.hotkey.ss58_address

if hotkey_ss58 in metagraph.hotkeys:
    uid = metagraph.hotkeys.index(hotkey_ss58)
    print(f'✅ Registered on subnet 98')
    print(f'UID: {uid}')
    print(f'Hotkey: {hotkey_ss58}')
    print(f'Stake: {metagraph.S[uid]} TAO')
else:
    print(f'❌ Not registered')
"
```

### Check Subnet 98 Status

```bash
python -c "
import bittensor as bt

subtensor = bt.subtensor(network='test')
metagraph = subtensor.metagraph(netuid=98)

print(f'Subnet 98 Statistics:')
print(f'Total neurons: {len(metagraph.uids)}')
print(f'Total stake: {metagraph.S.sum()} TAO')
print(f'Block: {subtensor.block}')
"
```

---

## Troubleshooting

### Issue 1: "ModuleNotFoundError: No module named 'bittensor'"

**Cause:** Virtual environment not activated or bittensor not installed

**Solution:**

```bash
source venv/bin/activate
pip install bittensor==9.12.2
```

---

### Issue 2: "TransactorAccountShouldBeHotKey" Error

**Cause:** You tried using PoW registration (which has a bug)

**Solution:** Use burned registration instead:

```bash
python register_burned.py
```

**Why this happens:**

- The error is misleading (it's NOT about funds)
- The SDK signs PoW registration with the wrong key
- Burned registration uses the correct key

---

### Issue 3: "No testnet TAO in wallet"

**Cause:** Wallet has 0 balance

**Solution:**

1. Check balance:

   ```bash
   python -c "
   import bittensor as bt
   wallet = bt.wallet(name='test_validator')
   s = bt.subtensor(network='test')
   print(s.get_balance(wallet.coldkeypub.ss58_address))
   "
   ```

2. Request testnet TAO (see [Getting Testnet TAO](#getting-testnet-tao))

---

### Issue 4: "Connection timeout to testnet"

**Cause:** Bittensor testnet endpoint issues or network problems

**Solution:**

1. Check internet connection
2. Try again in 5-10 minutes

---

### Issue 5: "Wallet already exists" Error

**Cause:** Trying to create wallet that already exists

**Solutions:**

**Option A:** Use existing wallet

```bash
# Just load it instead of creating
python -c "
import bittensor as bt
wallet = bt.wallet(name='test_validator', hotkey='test_hotkey')
print(f'Coldkey: {wallet.coldkeypub.ss58_address}')
print(f'Hotkey: {wallet.hotkey.ss58_address}')
"
```

**Option B:** Create with different name

```bash
# Use a new wallet name
python -c "
import bittensor as bt
wallet = bt.wallet(name='my_new_validator', hotkey='my_new_hotkey')
wallet.create_new_coldkey(use_password=False)
wallet.create_new_hotkey(use_password=False)
"
```

**Option C:** Overwrite existing (⚠️ DANGEROUS - You'll lose old keys!)

```python
wallet.create_new_coldkey(use_password=False, overwrite=True)
```

---

### Issue 6: "Subnet 98 does not exist"

**Cause:** Wrong network or subnet doesn't exist

**Solution:**

1. Verify subnet exists:

   ```bash
   python -c "
   import bittensor as bt
   s = bt.subtensor(network='test')
   exists = s.subnet_exists(netuid=98)
   print(f'Subnet 98 exists: {exists}')
   "
   ```

2. If it doesn't exist, contact subnet founder

---

### Issue 7: PyTorch Not Found

**Cause:** PyTorch not installed (needed for PoW registration only)

**Solution:**

```bash
pip install torch
```

**Note:** Not needed for burned registration (our recommended method)

---

## Next Steps

After successful registration, configure and deploy the validator:

### 1. Create Environment File

Create `.env` file in project root with the following configuration:

```bash
# Network Configuration
SUBTENSOR_NETWORK=test
NETUID=98

# Wallet Configuration
WALLET_NAME=test_validator
HOTKEY_NAME=test_hotkey

# Database Configuration
DB_CONNECTION_STRING

# Pool Configuration (BID/WETH pool on Aerodrome Base)
PAIR_ADDRESS=0x1024c20c048ea6087293f46d4a1c042cb6705924
CHAIN_ID=8453
START_BLOCK=35330091
TARGET_BLOCK=38634763
```

### 2. Database Schema Reference

The database contains historical pool events in separate tables:

| Table      | Records | Description                       |
| ---------- | ------- | --------------------------------- |
| `swaps`    | 10,641  | Token swap events with price data |
| `mints`    | 234     | Liquidity addition events         |
| `burns`    | 679     | Liquidity removal events          |
| `collects` | 682     | Fee collection events             |

**Important:** Pool addresses are stored **without the `0x` prefix** in the database.

### 3. Deploy Validator

```bash
source venv/bin/activate

# Run with --dry-run to test without publishing weights to chain
python -m validator.main \
  --wallet.name test_validator \
  --wallet.hotkey test_hotkey \
  --subtensor.network test \
  --netuid 98 \
  --pair_address 0x1024c20c048ea6087293f46d4a1c042cb6705924 \
  --target_block 38634763 \
  --start_block 35330091 \
  --dry-run
```

### 4. Running the Miner

Start the miner server before running the validator:

**Development Mode:**

```bash
source venv/bin/activate
python -m miner.miner
```

**Production Mode (Recommended):**

```bash
source venv/bin/activate
gunicorn -w 4 -b 0.0.0.0:8000 miner.miner:app
```

The miner will start on `http://localhost:8000` and expose:
- `GET /health` - Health check endpoint
- `POST /predict_strategy` - Strategy generation endpoint

### 5. Testing with Local Miner

To test with a local miner (bypassing metagraph lookup):

```bash
python -m validator.main \
  --wallet.name test_validator \
  --wallet.hotkey test_hotkey \
  --subtensor.network test \
  --netuid 98 \
  --pair_address 0x1024c20c048ea6087293f46d4a1c042cb6705924 \
  --target_block 38634763 \
  --start_block 35330091 \
  --test-miner http://localhost:8000 \
  --dry-run
```

### 6. Validator Flags Reference

| Flag                 | Description                                                           |
| -------------------- | --------------------------------------------------------------------- |
| `--dry-run`          | Skip weight publishing to chain (use until you have validator permit) |
| `--test-miner <url>` | Query a local miner directly instead of using metagraph               |

**Note:** The `--dry-run` flag is required until the subnet owner grants your validator a permit to publish weights. You'll see `NeuronNoValidatorPermit` error without it.

---

## Key Commands Reference

### Check Wallet Balance

```bash
python -c "
import bittensor as bt
w = bt.wallet(name='test_validator')
s = bt.subtensor(network='test')
print(f'Balance: {s.get_balance(w.coldkeypub.ss58_address)}')
"
```

### Check Registration Status

```bash
python -c "
import bittensor as bt
w = bt.wallet(name='test_validator', hotkey='test_hotkey')
s = bt.subtensor(network='test')
mg = s.metagraph(netuid=98)
if w.hotkey.ss58_address in mg.hotkeys:
    print(f'Registered! UID: {mg.hotkeys.index(w.hotkey.ss58_address)}')
else:
    print('Not registered')
"
```

### Check Subnet 98 Info

```bash
python -c "
import bittensor as bt
s = bt.subtensor(network='test')
mg = s.metagraph(netuid=98)
print(f'Total neurons: {len(mg.uids)}')
print(f'Total stake: {mg.S.sum()} TAO')
"
```

---

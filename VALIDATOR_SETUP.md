# SN98 ForeverMoney Validator Setup Guide

This comprehensive guide will walk you through setting up and running a validator for Subnet 98 (ForeverMoney) on Bittensor from scratch. This includes server provisioning, Bittensor registration, database configuration, and validator deployment.

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Server Setup](#server-setup)
4. [Bittensor Wallet Setup](#bittensor-wallet-setup)
5. [Validator Registration](#validator-registration)
6. [Database Setup](#database-setup)
7. [Validator Installation](#validator-installation)
8. [Configuration](#configuration)
9. [Running the Validator](#running-the-validator)
10. [Monitoring and Maintenance](#monitoring-and-maintenance)
11. [Troubleshooting](#troubleshooting)

---

## Overview

SN98 (ForeverMoney/九八) is a Bittensor subnet that optimizes liquidity provision strategies for Aerodrome v3 pools on Base. As a validator, you will:

- Generate round parameters for miners
- Poll miners for LP strategy submissions
- Backtest strategies using historical data
- Score strategies based on performance (70%) and LP alignment (30%)
- Publish winning strategies to the network
- Set weights for miners based on their scores

### System Requirements

**Minimum:**
- 4 CPU cores
- 16 GB RAM
- 100 GB SSD storage
- Ubuntu 20.04+ or Debian 11+
- Stable internet connection (100+ Mbps recommended)

**Recommended:**
- 8+ CPU cores
- 32 GB RAM
- 200 GB SSD storage
- Ubuntu 22.04 LTS
- 1 Gbps connection

---

## Prerequisites

Before starting, you'll need:

1. **TAO tokens** for:
   - Validator registration fee (varies by subnet)
   - Ongoing network fees

2. **Technical knowledge**:
   - Basic Linux command line
   - Understanding of SSH and server administration
   - Familiarity with Python environments

3. **Database access**:
   - Read-only Postgres credentials (provided by subnet owner)
   - Contact subnet owner for access

---

## Server Setup

### Option 1: AWS EC2

#### Step 1: Launch EC2 Instance

```bash
# Using AWS CLI
aws ec2 run-instances \
  --image-id ami-0c55b159cbfafe1f0 \  # Ubuntu 22.04 LTS
  --instance-type t3.xlarge \
  --key-name your-key-pair \
  --security-group-ids sg-xxxxxxxxx \
  --subnet-id subnet-xxxxxxxxx \
  --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":200,"VolumeType":"gp3"}}]' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=SN98-Validator}]'
```

#### Step 2: Configure Security Group

Allow inbound traffic:
- SSH (port 22) from your IP
- Custom TCP (port 9944) for Bittensor subtensor (if running local node)

#### Step 3: Connect to Instance

```bash
ssh -i your-key.pem ubuntu@your-instance-ip
```

### Option 2: Google Cloud Platform (GCP)

#### Step 1: Create Compute Engine Instance

```bash
# Using gcloud CLI
gcloud compute instances create sn98-validator \
  --zone=us-central1-a \
  --machine-type=n2-standard-8 \
  --boot-disk-size=200GB \
  --boot-disk-type=pd-ssd \
  --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud \
  --tags=validator
```

#### Step 2: Configure Firewall

```bash
gcloud compute firewall-rules create allow-validator \
  --allow=tcp:22,tcp:9944 \
  --source-ranges=YOUR_IP/32 \
  --target-tags=validator
```

#### Step 3: Connect

```bash
gcloud compute ssh sn98-validator --zone=us-central1-a
```

### Option 3: DigitalOcean

#### Step 1: Create Droplet

1. Log in to DigitalOcean
2. Click "Create" → "Droplets"
3. Choose:
   - **Image**: Ubuntu 22.04 LTS
   - **Plan**: Basic, 8 GB RAM / 4 CPUs ($48/mo) or Premium, 16 GB RAM / 8 CPUs
   - **Datacenter**: Choose closest to you
   - **Authentication**: SSH key (recommended)
4. Click "Create Droplet"

#### Step 2: Connect

```bash
ssh root@your-droplet-ip
```

### Option 4: Dedicated Server (Hetzner, OVH, etc.)

Similar to the above but with dedicated hardware. Follow provider-specific instructions for OS installation.

### Initial Server Configuration (All Providers)

Once connected to your server:

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install essential packages
sudo apt install -y build-essential git curl wget vim htop screen tmux

# Create non-root user (if not exists)
sudo adduser validator
sudo usermod -aG sudo validator

# Switch to validator user
su - validator

# Set timezone (optional)
sudo timedatectl set-timezone UTC
```

---

## Bittensor Wallet Setup

### Step 1: Install Python and Dependencies

```bash
# Install Python 3.10+
sudo apt install -y python3.10 python3.10-venv python3-pip

# Verify installation
python3 --version  # Should be 3.10+
```

### Step 2: Install Bittensor

```bash
# Create virtual environment
python3 -m venv ~/bittensor-env
source ~/bittensor-env/bin/activate

# Install bittensor
pip install --upgrade pip
pip install bittensor

# Verify installation
btcli --version
```

### Step 3: Create Wallet

```bash
# Create a new coldkey (stores your TAO)
btcli wallet new_coldkey --wallet.name validator_wallet

# Create a new hotkey (used for validator operations)
btcli wallet new_hotkey --wallet.name validator_wallet --wallet.hotkey validator_hotkey

# IMPORTANT: Backup your mnemonic phrases securely!
# Store them in a password manager or encrypted storage
```

### Step 4: Fund Your Wallet

You need TAO tokens to register as a validator.

```bash
# Check your coldkey address
btcli wallet overview --wallet.name validator_wallet

# Send TAO to this address from an exchange or another wallet
# Recommended: 100+ TAO for registration and operations
```

### Step 5: Verify Balance

```bash
# Check balance
btcli wallet balance --wallet.name validator_wallet

# Expected output:
# Coldkey: 5abc...xyz
# Balance: 100.000000 τ
```

---

## Validator Registration

### Step 1: Check Subnet Status

```bash
# View subnet 98 information
btcli subnet list | grep "98"

# Get detailed subnet info
btcli subnet info --netuid 98
```

### Step 2: Register Validator

```bash
# Register your validator on subnet 98
btcli subnet register --netuid 98 \
  --wallet.name validator_wallet \
  --wallet.hotkey validator_hotkey

# This will prompt for confirmation and cost TAO
# Follow the prompts to complete registration
```

### Step 3: Verify Registration

```bash
# Check if registered
btcli wallet overview --wallet.name validator_wallet --netuid 98

# You should see your validator listed with a UID
```

---

## Database Setup

SN98 validators require access to a read-only Postgres database containing historical pool data from the Aerodrome subgraph.

### Option 1: Use Provided Database (Recommended for MVP)

Contact the subnet owner to obtain:
- Database host
- Port (usually 5432)
- Database name
- Read-only username
- Password

Save these credentials securely - you'll need them for configuration.

### Option 2: Set Up Your Own Database (Advanced)

If you want to run your own subgraph indexer:

#### Step 1: Install PostgreSQL

```bash
# Install PostgreSQL 15
sudo apt install -y postgresql-15 postgresql-contrib-15

# Start PostgreSQL
sudo systemctl start postgresql
sudo systemctl enable postgresql
```

#### Step 2: Create Database

```bash
# Switch to postgres user
sudo -u postgres psql

# In PostgreSQL shell:
CREATE DATABASE sn98_pool_data;
CREATE USER readonly_user WITH PASSWORD 'your_secure_password';
GRANT CONNECT ON DATABASE sn98_pool_data TO readonly_user;

# Exit PostgreSQL
\q
```

#### Step 3: Set Up Subgraph Indexing

This requires running The Graph indexer node pointed at Base chain. See [The Graph documentation](https://thegraph.com/docs/en/) for detailed setup instructions.

**Note:** For MVP, using the provided database is strongly recommended.

### Test Database Connection

```bash
# Install PostgreSQL client
sudo apt install -y postgresql-client

# Test connection (using provided credentials)
psql -h <POSTGRES_HOST> -p 5432 -U readonly_user -d sn98_pool_data

# If successful, you'll see a prompt:
# sn98_pool_data=>

# Test query
SELECT COUNT(*) FROM pool_events;

# Exit
\q
```

---

## Validator Installation

### Step 1: Clone Repository

```bash
# Navigate to home directory
cd ~

# Clone the repository
git clone https://github.com/AuditBase/forever-money.git
cd forever-money
```

### Step 2: Create Virtual Environment

```bash
# Create virtual environment
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate

# Verify you're in the virtual environment
which python  # Should show ~/forever-money/venv/bin/python
```

### Step 3: Install Dependencies

```bash
# Upgrade pip
pip install --upgrade pip

# Install requirements
pip install -r requirements.txt

# Verify installation
python -c "import bittensor; print(bittensor.__version__)"
python -c "import psycopg2; print('PostgreSQL driver installed')"
```

### Step 4: Verify Installation

```bash
# Test validator import
python -c "from validator.validator import SN98Validator; print('Validator installed successfully')"
```

---

## Configuration

### Step 1: Create Environment File

```bash
# Copy example environment file
cp .env.example .env

# Edit with your favorite editor
nano .env  # or vim, emacs, etc.
```

### Step 2: Configure Environment Variables

Edit `.env` with your specific values:

```bash
# Postgres Database Configuration (provided by subnet owner)
POSTGRES_HOST=db.sn98.example.com
POSTGRES_PORT=5432
POSTGRES_DB=sn98_pool_data
POSTGRES_USER=readonly_user
POSTGRES_PASSWORD=your_provided_password

# Bittensor Configuration
NETUID=98
SUBTENSOR_NETWORK=finney  # or 'local' for testnet

# Validator Wallet (created earlier)
WALLET_NAME=validator_wallet
WALLET_HOTKEY=validator_hotkey

# Aerodrome Pool Configuration
PAIR_ADDRESS=0x0000000000000000000000000000000000000000  # Replace with actual pool
CHAIN_ID=8453  # Base mainnet

# Constraint Configuration
MAX_IL=0.10  # Maximum 10% impermanent loss
MIN_TICK_WIDTH=60  # Minimum tick width for positions
MAX_REBALANCES=4  # Maximum rebalances per round

# Scoring Parameters
PERFORMANCE_WEIGHT=0.7  # 70% weight on performance
LP_ALIGNMENT_WEIGHT=0.3  # 30% weight on LP fees
TOP_N_STRATEGIES=3  # Top 3 strategies get full weight

# Output Configuration
WINNING_STRATEGY_FILE=winning_strategy.json
```

### Step 3: Verify Configuration

```bash
# Test database connection with your config
python3 << EOF
from dotenv import load_dotenv
import os
from validator.database import PoolDataDB

load_dotenv()

db = PoolDataDB(
    host=os.getenv('POSTGRES_HOST'),
    port=int(os.getenv('POSTGRES_PORT')),
    database=os.getenv('POSTGRES_DB'),
    user=os.getenv('POSTGRES_USER'),
    password=os.getenv('POSTGRES_PASSWORD')
)

print("Database connection successful!")
EOF
```

### Step 4: Get Pool Address

You need the address of the Aerodrome v3 pool you want to target:

```bash
# Visit Aerodrome Finance on Base
# https://aerodrome.finance/liquidity

# Choose a v3 pool (e.g., WETH/USDC)
# Copy the pool address and add it to .env
```

---

## Running the Validator

### Option 1: Run Single Round (Testing)

For testing, run a single validation round:

```bash
# Activate virtual environment
source ~/forever-money/venv/bin/activate

# Run validator for a single round
python -m validator.main \
  --wallet.name validator_wallet \
  --wallet.hotkey validator_hotkey \
  --pair_address 0xYourPoolAddress \
  --target_block 12345678 \
  --start_block 12300000

# This will:
# 1. Generate round parameters
# 2. Poll all miners
# 3. Backtest strategies
# 4. Calculate scores
# 5. Publish weights
# 6. Save winning strategy to winning_strategy.json
```

### Option 2: Run Continuous Validator (Production)

For production, create a script to run rounds continuously:

#### Step 1: Create Validator Script

```bash
# Create script
cat > ~/forever-money/run_validator.sh << 'EOF'
#!/bin/bash

# Activate virtual environment
source ~/forever-money/venv/bin/activate

# Load environment
cd ~/forever-money
source .env

# Get current block from Base RPC
CURRENT_BLOCK=$(curl -s -X POST https://mainnet.base.org \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' \
  | jq -r '.result' | xargs printf "%d")

# Set target and start blocks
TARGET_BLOCK=$CURRENT_BLOCK
START_BLOCK=$((CURRENT_BLOCK - 50000))  # ~1 week of blocks

# Run validator
python -m validator.main \
  --wallet.name $WALLET_NAME \
  --wallet.hotkey $WALLET_HOTKEY \
  --pair_address $PAIR_ADDRESS \
  --target_block $TARGET_BLOCK \
  --start_block $START_BLOCK

# Exit
exit 0
EOF

# Make executable
chmod +x ~/forever-money/run_validator.sh
```

#### Step 2: Create Systemd Service

```bash
# Create service file
sudo tee /etc/systemd/system/sn98-validator.service > /dev/null << EOF
[Unit]
Description=SN98 ForeverMoney Validator
After=network.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=/home/$(whoami)/forever-money
ExecStart=/home/$(whoami)/forever-money/run_validator.sh
Restart=always
RestartSec=300
StandardOutput=append:/home/$(whoami)/forever-money/validator.log
StandardError=append:/home/$(whoami)/forever-money/validator_error.log

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd
sudo systemctl daemon-reload

# Enable service
sudo systemctl enable sn98-validator

# Start service
sudo systemctl start sn98-validator
```

#### Step 3: Verify Service

```bash
# Check service status
sudo systemctl status sn98-validator

# View logs
tail -f ~/forever-money/validator.log

# Stop service (if needed)
sudo systemctl stop sn98-validator

# Restart service
sudo systemctl restart sn98-validator
```

### Option 3: Run with Screen/Tmux (Alternative)

If you prefer screen or tmux:

```bash
# Using screen
screen -S validator
source ~/forever-money/venv/bin/activate
cd ~/forever-money

# Run validator loop
while true; do
  ./run_validator.sh
  echo "Round completed. Waiting 1 hour before next round..."
  sleep 3600
done

# Detach: Ctrl+A then D
# Reattach: screen -r validator
```

---

## Monitoring and Maintenance

### Log Monitoring

```bash
# Real-time log viewing
tail -f ~/forever-money/validator.log

# Search logs for errors
grep ERROR ~/forever-money/validator.log

# View recent activity
tail -n 100 ~/forever-money/validator.log
```

### Performance Metrics

Monitor your validator's performance:

```bash
# Check validator status on network
btcli wallet overview --wallet.name validator_wallet --netuid 98

# View your weights
btcli subnet list-weights --netuid 98 | grep $(btcli wallet overview --wallet.name validator_wallet | grep "Hotkey" | awk '{print $2}')

# Check emissions
btcli wallet overview --wallet.name validator_wallet --netuid 98 | grep "Emission"
```

### System Resources

```bash
# Monitor CPU and RAM
htop

# Check disk usage
df -h

# Monitor network
iftop  # May need to install: sudo apt install iftop

# Check Python process
ps aux | grep python | grep validator
```

### Database Monitoring

```bash
# Test database connection
psql -h $POSTGRES_HOST -p 5432 -U readonly_user -d sn98_pool_data -c "SELECT COUNT(*) FROM pool_events;"

# Check recent events
psql -h $POSTGRES_HOST -p 5432 -U readonly_user -d sn98_pool_data -c "SELECT * FROM pool_events ORDER BY block_number DESC LIMIT 10;"
```

### Automated Alerts (Optional)

Set up monitoring with Prometheus, Grafana, or simple email alerts:

```bash
# Install monitoring script
cat > ~/forever-money/monitor.sh << 'EOF'
#!/bin/bash

# Check if validator is running
if ! pgrep -f "validator.main" > /dev/null; then
  echo "ALERT: Validator not running!" | mail -s "SN98 Validator Alert" your@email.com
  sudo systemctl restart sn98-validator
fi

# Check disk space
DISK_USAGE=$(df -h / | tail -1 | awk '{print $5}' | sed 's/%//')
if [ $DISK_USAGE -gt 85 ]; then
  echo "ALERT: Disk usage at ${DISK_USAGE}%" | mail -s "SN98 Disk Alert" your@email.com
fi
EOF

chmod +x ~/forever-money/monitor.sh

# Add to crontab (runs every 10 minutes)
crontab -e
# Add line:
# */10 * * * * /home/validator/forever-money/monitor.sh
```

### Regular Maintenance

**Daily:**
- Check validator logs for errors
- Verify service is running
- Monitor system resources

**Weekly:**
- Review validator performance metrics
- Check for software updates
- Review winning strategies output

**Monthly:**
- Update dependencies: `pip install --upgrade -r requirements.txt`
- Review and rotate logs if needed
- Check TAO balance for network fees

---

## Troubleshooting

### Issue 1: Validator Not Starting

**Symptoms:** Service fails to start or crashes immediately

**Solutions:**
```bash
# Check logs for specific error
sudo journalctl -u sn98-validator -n 50

# Verify Python environment
source ~/forever-money/venv/bin/activate
python -c "from validator.validator import SN98Validator"

# Check environment variables
cd ~/forever-money
source .env
echo $POSTGRES_HOST  # Should not be empty

# Test database connection
python -c "from validator.database import PoolDataDB; import os; from dotenv import load_dotenv; load_dotenv(); db = PoolDataDB(os.getenv('POSTGRES_HOST'), int(os.getenv('POSTGRES_PORT')), os.getenv('POSTGRES_DB'), os.getenv('POSTGRES_USER'), os.getenv('POSTGRES_PASSWORD')); print('OK')"
```

### Issue 2: Cannot Connect to Database

**Symptoms:** "Connection refused" or "Connection timeout" errors

**Solutions:**
```bash
# Check if host is reachable
ping $POSTGRES_HOST

# Test port connectivity
telnet $POSTGRES_HOST 5432
# or
nc -zv $POSTGRES_HOST 5432

# Verify credentials
psql -h $POSTGRES_HOST -p 5432 -U $POSTGRES_USER -d $POSTGRES_DB

# Check firewall rules
sudo ufw status  # If using ufw
```

### Issue 3: No Miner Responses

**Symptoms:** Validator polls but receives no responses

**Solutions:**
```bash
# Sync metagraph
btcli subnet metagraph --netuid 98

# Check active miners
btcli subnet list --netuid 98

# Verify network connectivity
curl -I http://api.bittensor.com

# Check if miners are registered
python -c "import bittensor as bt; sub = bt.subtensor('finney'); meta = sub.metagraph(98); print(f'Active UIDs: {[i for i, axon in enumerate(meta.axons) if axon.is_serving]}')"
```

### Issue 4: Bittensor Wallet Issues

**Symptoms:** "Wallet not found" or "Insufficient balance" errors

**Solutions:**
```bash
# List wallets
btcli wallet list

# Check specific wallet
btcli wallet overview --wallet.name validator_wallet

# Regenerate from mnemonic (if needed)
btcli wallet regen_coldkey --wallet.name validator_wallet --mnemonic "your twelve word mnemonic phrase here"

# Check TAO balance
btcli wallet balance --wallet.name validator_wallet
```

### Issue 5: High Memory Usage

**Symptoms:** Out of memory errors, system slowdown

**Solutions:**
```bash
# Check memory usage
free -h

# Identify memory-hungry processes
ps aux --sort=-%mem | head

# Increase swap (temporary fix)
sudo fallocate -l 8G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# Add to fstab for persistence
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# Consider upgrading server RAM
```

### Issue 6: Invalid Strategies / Zero Scores

**Symptoms:** All miners receiving score of 0

**Solutions:**
```bash
# Check constraint parameters in .env
grep -E "MAX_IL|MIN_TICK_WIDTH|MAX_REBALANCES" .env

# Verify constraints are reasonable
# MAX_IL: 0.05-0.20 (5-20%)
# MIN_TICK_WIDTH: 60-200 ticks
# MAX_REBALANCES: 2-10

# Review backtester logs
grep "constraint violation" ~/forever-money/validator.log
```

### Issue 7: Subtensor Connection Issues

**Symptoms:** Cannot connect to Bittensor network

**Solutions:**
```bash
# Test subtensor connection
python -c "import bittensor as bt; sub = bt.subtensor('finney'); print(f'Connected: {sub.is_connected()}')"

# Use different endpoint
export SUBTENSOR_ENDPOINT=ws://127.0.0.1:9944  # If running local
# or
export SUBTENSOR_ENDPOINT=wss://entrypoint-finney.opentensor.ai:443

# Check Bittensor network status
# Visit: https://taostats.io
```

### Issue 8: Permission Denied Errors

**Symptoms:** Permission errors when accessing files

**Solutions:**
```bash
# Fix ownership
sudo chown -R $(whoami):$(whoami) ~/forever-money

# Fix permissions
chmod +x ~/forever-money/run_validator.sh
chmod 600 ~/.env  # Protect sensitive config

# If using systemd, verify user
sudo systemctl cat sn98-validator | grep User
```

### Getting Help

If you're still experiencing issues:

1. **Check GitHub Issues:** [AuditBase/forever-money/issues](https://github.com/AuditBase/forever-money/issues)
2. **Review Logs:** Always include relevant log excerpts when asking for help
3. **Bittensor Discord:** Join the Bittensor community Discord for subnet support
4. **Contact Subnet Owner:** For database access or subnet-specific issues

---

## Security Best Practices

1. **Protect Your Keys:**
   - Never share mnemonic phrases
   - Use encrypted storage for backups
   - Consider hardware wallets for coldkeys

2. **Secure Your Server:**
   ```bash
   # Enable firewall
   sudo ufw enable
   sudo ufw allow 22/tcp  # SSH
   sudo ufw allow 9944/tcp  # Subtensor (if needed)

   # Disable root SSH
   sudo sed -i 's/PermitRootLogin yes/PermitRootLogin no/' /etc/ssh/sshd_config
   sudo systemctl restart sshd

   # Keep system updated
   sudo apt update && sudo apt upgrade -y
   ```

3. **Use SSH Keys:**
   - Disable password authentication
   - Use SSH keys only
   - Consider fail2ban for brute force protection

4. **Regular Backups:**
   - Backup wallet files regularly
   - Store backups in multiple secure locations
   - Test recovery procedures

5. **Monitor Access:**
   ```bash
   # Review login attempts
   sudo tail -f /var/log/auth.log

   # Check active sessions
   who
   ```

---

## Advanced Configuration

### Running Multiple Validators

To run validators for multiple subnets:

```bash
# Create separate directories
mkdir -p ~/validators/sn98
mkdir -p ~/validators/sn{other}

# Use different configs
cp ~/forever-money/.env ~/validators/sn98/.env
# Edit each .env with different settings
```

### Custom Scoring Parameters

Adjust scoring in `.env`:

```bash
# More weight on performance
PERFORMANCE_WEIGHT=0.8
LP_ALIGNMENT_WEIGHT=0.2

# Reward more top strategies
TOP_N_STRATEGIES=5

# Stricter constraints
MAX_IL=0.05  # Only 5% IL allowed
MIN_TICK_WIDTH=100  # Wider ranges required
```

### Integration with Executor Bot

The validator outputs winning strategies to `winning_strategy.json`. If you're also running the Executor Bot:

```bash
# Configure executor to read from validator output
# See EXECUTOR_SETUP.md (when available)
```

---

## FAQ

**Q: How much TAO do I need to run a validator?**
A: You need enough TAO for registration (varies by subnet) plus ongoing network fees. Recommended: 100+ TAO.

**Q: Can I run a validator on Windows?**
A: It's possible but not recommended. Use Linux (Ubuntu 22.04) for best compatibility.

**Q: How often should validation rounds run?**
A: For MVP, running rounds every 1-6 hours is typical. Adjust based on network activity.

**Q: What if I lose my mnemonic phrase?**
A: Your funds will be permanently lost. Always backup securely!

**Q: Can I run validator and miner on the same machine?**
A: Yes, but ensure adequate resources (32+ GB RAM recommended).

**Q: How do I upgrade the validator software?**
A:
```bash
cd ~/forever-money
git pull origin main
source venv/bin/activate
pip install -r requirements.txt --upgrade
sudo systemctl restart sn98-validator
```

**Q: Where can I see my validator's performance?**
A: Use `btcli wallet overview` or visit https://taostats.io for network-wide statistics.

---

## Conclusion

You should now have a fully operational SN98 ForeverMoney validator! Remember to:

- Monitor your validator regularly
- Keep software updated
- Secure your keys and server
- Join the community for updates

For additional support, refer to:
- [README.md](README.md) - General project information
- [QUICKSTART.md](QUICKSTART.md) - Quick reference guide
- [spec.md](spec.md) - Technical specification
- [GitHub Issues](https://github.com/AuditBase/forever-money/issues) - Report problems

Happy validating! 🚀

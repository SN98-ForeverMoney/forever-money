# SN98 ForeverMoney Miner Setup Guide

This comprehensive guide will walk you through setting up and running a miner for Subnet 98 (ForeverMoney) on Bittensor from scratch. This includes server provisioning, Bittensor registration, strategy development, and competitive optimization techniques.

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Understanding the Competition](#understanding-the-competition)
4. [Server Setup](#server-setup)
5. [Bittensor Wallet Setup](#bittensor-wallet-setup)
6. [Miner Registration](#miner-registration)
7. [Miner Installation](#miner-installation)
8. [Configuration](#configuration)
9. [Strategy Development](#strategy-development)
10. [Running the Miner](#running-the-miner)
11. [Gaining a Competitive Edge](#gaining-a-competitive-edge)
12. [Monitoring and Optimization](#monitoring-and-optimization)
13. [Troubleshooting](#troubleshooting)

---

## Overview

SN98 (ForeverMoney/九八) is a competitive Bittensor subnet that rewards miners for proposing optimal liquidity provision strategies for Aerodrome v3 pools on Base. As a miner, you will:

- Receive round parameters from validators
- Query historical pool data from a provided Postgres database
- Generate optimal LP position configurations
- Return strategies that maximize performance while meeting constraints
- Compete against other miners for rewards

### How Scoring Works

Validators score your strategies using a weighted system:

1. **Net PnL vs HODL (70%)**
   - Your strategy's performance is backtested against passive holding
   - **Top 3 strategies receive full weight** - this is a competitive, winner-takes-most system
   - Lower-ranked strategies receive reduced or zero weight

2. **LP Fee Share (30%)**
   - Fees generated from your own liquidity vaults (if you contribute liquidity)
   - Scored pro-rata based on your contribution
   - **Note:** For MVP, this component may be zero if you don't provide liquidity

**Final Score = (Performance × 0.7) + (LP Alignment × 0.3)**

### System Requirements

**Minimum:**
- 2 CPU cores
- 8 GB RAM
- 50 GB SSD storage
- Ubuntu 20.04+ or Debian 11+
- Stable internet connection (100+ Mbps recommended)

**Recommended:**
- 4+ CPU cores
- 16 GB RAM
- 100 GB SSD storage
- Ubuntu 22.04 LTS
- GPU access (for ML-based strategies)
- 1 Gbps connection

---

## Prerequisites

Before starting, you'll need:

1. **TAO tokens** for:
   - Miner registration fee (varies by subnet)
   - Initial stake to be competitive

2. **Technical knowledge**:
   - Basic Linux command line
   - Understanding of SSH and server administration
   - Python programming (intermediate level)
   - Familiarity with DeFi concepts (LPs, impermanent loss, tick ranges)
   - Optional: Machine learning for advanced strategies

3. **Database access**:
   - Validators will provide read-only Postgres credentials in each round request
   - No setup required on your end

---

## Understanding the Competition

### The Competitive Landscape

SN98 is a **winner-takes-most competition**. Only the top 3 strategies receive full weight for 70% of the score. This means:

- **High competition**: You're competing against sophisticated strategies
- **Strategy quality matters more than uptime**: A great strategy beats a mediocre always-on miner
- **Continuous improvement required**: Other miners will adapt and improve

### What Makes a Winning Strategy?

1. **Maximizes fee capture**: Positions should be in active trading ranges
2. **Minimizes impermanent loss**: Stay within the `max_il` constraint (typically 10%)
3. **Optimal tick ranges**: Balance fee concentration with risk
4. **Smart rebalancing**: Adapt to market conditions without over-trading
5. **Constraint compliance**: All constraints must be met or you score 0

### Common Pitfalls to Avoid

- **Over-optimization to historical data**: Strategies that overfit will fail on new data
- **Ignoring constraints**: Even 1% over the IL limit = zero score
- **Too narrow ranges**: High fees but massive IL when price moves
- **Too wide ranges**: Low IL but minimal fee capture
- **Excessive rebalancing**: Costs and complexity without performance gains

---

## Server Setup

### Option 1: AWS EC2

#### Step 1: Launch EC2 Instance

```bash
# Using AWS CLI
aws ec2 run-instances \
  --image-id ami-0c55b159cbfafe1f0 \  # Ubuntu 22.04 LTS
  --instance-type t3.medium \
  --key-name your-key-pair \
  --security-group-ids sg-xxxxxxxxx \
  --subnet-id subnet-xxxxxxxxx \
  --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":100,"VolumeType":"gp3"}}]' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=SN98-Miner}]'
```

#### Step 2: Configure Security Group

Allow inbound traffic:
- SSH (port 22) from your IP
- HTTP (port 8000) from validator IPs (or 0.0.0.0/0 for simplicity)
- HTTPS (port 443) if using SSL

#### Step 3: Connect to Instance

```bash
ssh -i your-key.pem ubuntu@your-instance-ip
```

### Option 2: Google Cloud Platform (GCP)

#### Step 1: Create Compute Engine Instance

```bash
# Using gcloud CLI
gcloud compute instances create sn98-miner \
  --zone=us-central1-a \
  --machine-type=e2-standard-4 \
  --boot-disk-size=100GB \
  --boot-disk-type=pd-ssd \
  --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud \
  --tags=miner
```

#### Step 2: Configure Firewall

```bash
gcloud compute firewall-rules create allow-miner \
  --allow=tcp:22,tcp:8000,tcp:443 \
  --source-ranges=0.0.0.0/0 \
  --target-tags=miner
```

#### Step 3: Connect

```bash
gcloud compute ssh sn98-miner --zone=us-central1-a
```

### Option 3: DigitalOcean

#### Step 1: Create Droplet

1. Log in to DigitalOcean
2. Click "Create" → "Droplets"
3. Choose:
   - **Image**: Ubuntu 22.04 LTS
   - **Plan**: Basic, 4 GB RAM / 2 CPUs ($24/mo) or 8 GB RAM / 4 CPUs ($48/mo)
   - **Datacenter**: Choose closest to Base RPC endpoints (US East or West)
   - **Authentication**: SSH key (recommended)
4. Click "Create Droplet"

#### Step 2: Connect

```bash
ssh root@your-droplet-ip
```

### Option 4: Hetzner (Cost-Effective European Option)

Hetzner offers excellent price/performance for miners:

```bash
# Via Hetzner Cloud Console:
# 1. Create project
# 2. Add server
# 3. Choose CPX31 (4 vCPU, 8GB RAM, €15/mo) or CCX23 (4 dedicated cores, €28/mo)
# 4. Select Ubuntu 22.04
# 5. Add SSH key
# 6. Create & connect

ssh root@your-server-ip
```

### Option 5: Lambda Labs / RunPod (GPU Instances)

For ML-based strategies requiring GPU:

**Lambda Labs:**
```bash
# Via web console:
# 1. Create instance with A10 GPU (~$0.60/hr)
# 2. Choose PyTorch environment
# 3. Connect via SSH
```

**RunPod:**
```bash
# 1. Select GPU (RTX 4090, A40, etc.)
# 2. Choose PyTorch template
# 3. Expose port 8000
# 4. Connect via SSH
```

### Initial Server Configuration (All Providers)

Once connected to your server:

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install essential packages
sudo apt install -y build-essential git curl wget vim htop screen tmux \
  python3-pip python3-venv postgresql-client jq net-tools

# Create non-root user (if not exists)
sudo adduser miner
sudo usermod -aG sudo miner

# Switch to miner user
su - miner

# Set timezone (optional)
sudo timedatectl set-timezone UTC

# Configure swap (recommended for memory-intensive strategies)
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
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
btcli wallet new_coldkey --wallet.name miner_wallet

# Create a new hotkey (used for miner operations)
btcli wallet new_hotkey --wallet.name miner_wallet --wallet.hotkey miner_hotkey

# IMPORTANT: Backup your mnemonic phrases securely!
# Store them in a password manager or encrypted storage
# Write them down on paper and store in a safe location
```

### Step 4: Fund Your Wallet

You need TAO tokens to register as a miner.

```bash
# Check your coldkey address
btcli wallet overview --wallet.name miner_wallet

# Send TAO to this address from an exchange or another wallet
# Recommended: 50+ TAO for registration and operations
```

### Step 5: Verify Balance

```bash
# Check balance
btcli wallet balance --wallet.name miner_wallet

# Expected output:
# Coldkey: 5abc...xyz
# Balance: 50.000000 τ
```

---

## Miner Registration

### Step 1: Check Subnet Status

```bash
# View subnet 98 information
btcli subnet list | grep "98"

# Get detailed subnet info
btcli subnet info --netuid 98

# Check registration cost
btcli subnet register --netuid 98 --wallet.name miner_wallet --wallet.hotkey miner_hotkey --help
```

### Step 2: Register Miner

```bash
# Register your miner on subnet 98
btcli subnet register --netuid 98 \
  --wallet.name miner_wallet \
  --wallet.hotkey miner_hotkey

# This will prompt for confirmation and cost TAO
# Follow the prompts to complete registration
```

### Step 3: Verify Registration

```bash
# Check if registered
btcli wallet overview --wallet.name miner_wallet --netuid 98

# You should see your miner listed with a UID
# Note your UID - you'll need it for monitoring
```

---

## Miner Installation

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

# Install additional packages for strategy development
pip install numpy pandas scikit-learn scipy

# For ML-based strategies (optional)
pip install torch transformers

# Verify installation
python -c "import bittensor; print(bittensor.__version__)"
python -c "import psycopg2; print('PostgreSQL driver installed')"
python -c "from flask import Flask; print('Flask installed')"
```

### Step 4: Verify Installation

```bash
# Test miner import
python -c "from miner.miner import app; print('Miner installed successfully')"
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
# Miner Configuration
MINER_PORT=8000
MINER_VERSION=1.0.0
MODEL_INFO=your-strategy-name  # e.g., "LSTM-v3-optimized"

# Bittensor Configuration
NETUID=98
SUBTENSOR_NETWORK=finney  # or 'local' for testnet

# Miner Wallet (created earlier)
WALLET_NAME=miner_wallet
WALLET_HOTKEY=miner_hotkey

# Strategy Parameters (customize based on your approach)
DEFAULT_CONFIDENCE=0.85
RISK_TOLERANCE=medium  # low, medium, high

# Database Configuration (will be provided by validators in requests)
# You don't need to configure this - it's provided per-round
```

### Step 3: Test Configuration

```bash
# Test basic imports
python3 << 'EOF'
from dotenv import load_dotenv
import os

load_dotenv()

print(f"Miner Port: {os.getenv('MINER_PORT')}")
print(f"Miner Version: {os.getenv('MINER_VERSION')}")
print(f"Model Info: {os.getenv('MODEL_INFO')}")
print("Configuration loaded successfully!")
EOF
```

---

## Strategy Development

This is the most critical part of being a competitive miner. Your strategy determines your rewards.

### Understanding the Data

Validators provide access to a Postgres database containing:

```sql
-- Table: pool_events
-- Columns:
--   block_number: Block number
--   transaction_hash: Transaction hash
--   pool_address: Pool/pair address
--   event_type: 'swap', 'mint', 'burn', 'collect'
--   event_data: JSONB with event-specific data
--   timestamp: Block timestamp

-- Example queries you might use:

-- Get recent swaps
SELECT * FROM pool_events
WHERE event_type = 'swap'
  AND pool_address = '0x...'
  AND block_number > 12000000
ORDER BY block_number DESC
LIMIT 1000;

-- Calculate fee generation by tick range
SELECT
  (event_data->>'tickLower')::int as tick_lower,
  (event_data->>'tickUpper')::int as tick_upper,
  SUM((event_data->>'fee')::numeric) as total_fees
FROM pool_events
WHERE event_type = 'collect'
  AND pool_address = '0x...'
GROUP BY tick_lower, tick_upper
ORDER BY total_fees DESC;

-- Analyze price volatility
SELECT
  block_number,
  (event_data->>'sqrtPriceX96')::numeric as price
FROM pool_events
WHERE event_type = 'swap'
  AND pool_address = '0x...'
ORDER BY block_number;
```

### Strategy Types

#### 1. Rule-Based Strategy (Beginner)

Simple rules based on historical patterns:

```python
from miner.strategy import SimpleStrategyGenerator
from validator.models import ValidatorRequest, Strategy, Position

class RuleBasedStrategy(SimpleStrategyGenerator):
    def generate_strategy(self, request: ValidatorRequest) -> Strategy:
        # Simple rule: split inventory into 2 positions
        # - Tight range for high fees
        # - Wide range for stability

        constraints = request.metadata.constraints
        inventory = request.inventory

        positions = [
            Position(
                tickLower=-1200,
                tickUpper=-600,
                allocation0=str(int(inventory.amount0) * 2 // 3),
                allocation1=str(int(inventory.amount1) * 2 // 3),
                confidence=0.75
            ),
            Position(
                tickLower=-2400,
                tickUpper=0,
                allocation0=str(int(inventory.amount0) // 3),
                allocation1=str(int(inventory.amount1) // 3),
                confidence=0.85
            )
        ]

        return Strategy(
            positions=positions,
            rebalance_rule={
                "trigger": "price_outside_range",
                "cooldown_blocks": 1800
            }
        )
```

#### 2. Statistical Strategy (Intermediate)

Uses historical data analysis:

```python
import psycopg2
import numpy as np
from scipy import stats

class StatisticalStrategy(SimpleStrategyGenerator):
    def generate_strategy(self, request: ValidatorRequest) -> Strategy:
        # Connect to database
        db = psycopg2.connect(
            host=request.postgres_access['host'],
            port=request.postgres_access['port'],
            dbname=request.postgres_access['database'],
            user=request.postgres_access['user'],
            password=request.postgres_access['password']
        )

        # Fetch historical swap data
        cursor = db.cursor()
        cursor.execute("""
            SELECT
                (event_data->>'sqrtPriceX96')::numeric as price,
                block_number
            FROM pool_events
            WHERE event_type = 'swap'
              AND pool_address = %s
              AND block_number <= %s
              AND block_number > %s - 50000
            ORDER BY block_number
        """, (request.pairAddress, request.target_block, request.target_block))

        prices = [row[0] for row in cursor.fetchall()]

        # Calculate statistics
        mean_price = np.mean(prices)
        std_price = np.std(prices)

        # Set ranges based on 1 and 2 standard deviations
        # Convert price to ticks (simplified)
        mean_tick = int(np.log(mean_price) * 10000)
        std_tick = int(std_price / mean_price * 10000)

        positions = [
            Position(
                tickLower=mean_tick - std_tick,
                tickUpper=mean_tick + std_tick,
                allocation0=str(int(request.inventory.amount0) * 3 // 4),
                allocation1=str(int(request.inventory.amount1) * 3 // 4),
                confidence=0.80
            ),
            Position(
                tickLower=mean_tick - 2*std_tick,
                tickUpper=mean_tick + 2*std_tick,
                allocation0=str(int(request.inventory.amount0) // 4),
                allocation1=str(int(request.inventory.amount1) // 4),
                confidence=0.90
            )
        ]

        db.close()

        return Strategy(positions=positions)
```

#### 3. Machine Learning Strategy (Advanced)

Uses ML models to predict optimal ranges:

```python
import torch
import torch.nn as nn
from sklearn.preprocessing import StandardScaler

class LSTMPricePredictor(nn.Module):
    def __init__(self, input_size=10, hidden_size=64, num_layers=2):
        super().__init__()
        self.lstm = nn.LSTM(input_size, hidden_size, num_layers, batch_first=True)
        self.fc = nn.Linear(hidden_size, 2)  # Predict next price range

    def forward(self, x):
        lstm_out, _ = self.lstm(x)
        return self.fc(lstm_out[:, -1, :])

class MLStrategy(SimpleStrategyGenerator):
    def __init__(self):
        super().__init__()
        self.model = LSTMPricePredictor()
        # Load pre-trained weights
        self.model.load_state_dict(torch.load('models/lstm_price_predictor.pth'))
        self.model.eval()
        self.scaler = StandardScaler()

    def generate_strategy(self, request: ValidatorRequest) -> Strategy:
        # Fetch and preprocess data
        historical_data = self.fetch_historical_features(request)

        # Predict optimal ranges
        with torch.no_grad():
            prediction = self.model(historical_data)
            predicted_lower = prediction[0][0].item()
            predicted_upper = prediction[0][1].item()

        # Convert predictions to tick ranges
        # (implementation details omitted)

        # Generate positions based on predictions
        positions = self.create_positions_from_predictions(
            predicted_lower, predicted_upper, request
        )

        return Strategy(positions=positions)
```

### Constraint Validation

**Critical**: Always validate constraints before returning:

```python
def validate_constraints(self, strategy: Strategy, constraints: dict) -> bool:
    """
    Ensure your strategy meets all constraints.
    Non-compliant strategies score ZERO.
    """
    # Check tick width
    for pos in strategy.positions:
        tick_width = pos.tickUpper - pos.tickLower
        if tick_width < constraints['min_tick_width']:
            return False

    # Check rebalances
    if strategy.rebalance_rule:
        # Ensure rebalance count won't exceed max
        pass

    # Check impermanent loss (requires simulation)
    estimated_il = self.calculate_max_il(strategy)
    if estimated_il > constraints['max_il']:
        return False

    return True
```

### Backtesting Your Strategy

Before deploying, backtest locally:

```python
# Create a local backtester
from validator.backtester import Backtester

backtester = Backtester(db_connection)

# Test your strategy
result = backtester.backtest_strategy(
    strategy=your_strategy,
    start_block=12000000,
    end_block=12100000,
    initial_amount0=1000000000000000000,
    initial_amount1=2500000000
)

print(f"PnL vs HODL: {result.pnl_vs_hodl}%")
print(f"Total Fees: {result.total_fees}")
print(f"Max IL: {result.max_il}%")
```

---

## Running the Miner

### Option 1: Development Mode (Testing)

For local testing and development:

```bash
# Activate virtual environment
source ~/forever-money/venv/bin/activate

# Run miner server
python -m miner.miner

# Output:
# * Running on http://0.0.0.0:8000
# * Serving Flask app 'miner.miner'
```

Test your endpoint:

```bash
# In another terminal
curl -X POST http://localhost:8000/predict_strategy \
  -H "Content-Type: application/json" \
  -d '{
    "pairAddress": "0x...",
    "chainId": 8453,
    "target_block": 12345678,
    "mode": "inventory",
    "inventory": {"amount0": "1000000000", "amount1": "2000000000"},
    "metadata": {
      "round_id": "test",
      "constraints": {"max_il": 0.10, "min_tick_width": 60, "max_rebalances": 4}
    },
    "postgres_access": {}
  }'
```

### Option 2: Production with Gunicorn

For production, use a production WSGI server:

```bash
# Install gunicorn
pip install gunicorn

# Run with gunicorn
gunicorn -w 4 -b 0.0.0.0:8000 --timeout 120 miner.miner:app

# Options explained:
# -w 4: 4 worker processes
# -b 0.0.0.0:8000: Bind to all interfaces on port 8000
# --timeout 120: 120 second timeout for requests
```

### Option 3: Systemd Service (Recommended for Production)

#### Step 1: Create Service File

```bash
# Create service file
sudo tee /etc/systemd/system/sn98-miner.service > /dev/null << EOF
[Unit]
Description=SN98 ForeverMoney Miner
After=network.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=/home/$(whoami)/forever-money
Environment="PATH=/home/$(whoami)/forever-money/venv/bin"
ExecStart=/home/$(whoami)/forever-money/venv/bin/gunicorn -w 4 -b 0.0.0.0:8000 --timeout 120 miner.miner:app
Restart=always
RestartSec=10
StandardOutput=append:/home/$(whoami)/forever-money/miner.log
StandardError=append:/home/$(whoami)/forever-money/miner_error.log

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd
sudo systemctl daemon-reload

# Enable service
sudo systemctl enable sn98-miner

# Start service
sudo systemctl start sn98-miner
```

#### Step 2: Verify Service

```bash
# Check service status
sudo systemctl status sn98-miner

# View logs
tail -f ~/forever-money/miner.log

# Stop service (if needed)
sudo systemctl stop sn98-miner

# Restart service
sudo systemctl restart sn98-miner
```

### Option 4: Docker Deployment (Alternative)

Create a Dockerfile:

```dockerfile
FROM python:3.10-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Copy application
COPY . .

# Expose port
EXPOSE 8000

# Run gunicorn
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:8000", "--timeout", "120", "miner.miner:app"]
```

Build and run:

```bash
# Build image
docker build -t sn98-miner .

# Run container
docker run -d \
  --name sn98-miner \
  -p 8000:8000 \
  --restart unless-stopped \
  sn98-miner

# View logs
docker logs -f sn98-miner
```

### Option 5: With Nginx Reverse Proxy (Production Best Practice)

#### Step 1: Install Nginx

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
```

#### Step 2: Configure Nginx

```bash
sudo tee /etc/nginx/sites-available/sn98-miner > /dev/null << 'EOF'
server {
    listen 80;
    server_name your-domain.com;  # Replace with your domain or IP

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_connect_timeout 120s;
        proxy_send_timeout 120s;
        proxy_read_timeout 120s;
    }
}
EOF

# Enable site
sudo ln -s /etc/nginx/sites-available/sn98-miner /etc/nginx/sites-enabled/

# Test configuration
sudo nginx -t

# Restart nginx
sudo systemctl restart nginx
```

#### Step 3: Enable HTTPS (Optional but Recommended)

```bash
# Get SSL certificate
sudo certbot --nginx -d your-domain.com

# Auto-renewal is configured automatically
```

---

## Gaining a Competitive Edge

### 1. Data Analysis and Feature Engineering

The more insights you extract from historical data, the better:

```python
# Advanced feature engineering
def extract_advanced_features(db_connection, pool_address, target_block):
    """
    Extract sophisticated features from historical data.
    """
    features = {}

    # 1. Volatility measures
    features['volatility_7d'] = calculate_volatility(db_connection, pool_address, days=7)
    features['volatility_30d'] = calculate_volatility(db_connection, pool_address, days=30)

    # 2. Fee concentration analysis
    features['fee_concentration_gini'] = calculate_fee_gini(db_connection, pool_address)

    # 3. Liquidity depth by tick range
    features['liquidity_distribution'] = analyze_liquidity_distribution(db_connection, pool_address)

    # 4. Trading pattern analysis
    features['avg_swap_size'] = calculate_avg_swap_size(db_connection, pool_address)
    features['trading_frequency'] = calculate_trading_frequency(db_connection, pool_address)

    # 5. Time-based patterns
    features['peak_trading_hours'] = identify_peak_hours(db_connection, pool_address)

    # 6. Trend analysis
    features['price_trend'] = calculate_price_trend(db_connection, pool_address)

    return features
```

### 2. Ensemble Strategies

Combine multiple approaches:

```python
class EnsembleStrategy:
    def __init__(self):
        self.strategies = [
            RuleBasedStrategy(),
            StatisticalStrategy(),
            MLStrategy()
        ]
        self.weights = [0.3, 0.3, 0.4]

    def generate_strategy(self, request):
        # Generate strategies from all models
        all_strategies = [s.generate_strategy(request) for s in self.strategies]

        # Combine using weighted average of positions
        combined = self.combine_strategies(all_strategies, self.weights)

        return combined
```

### 3. Real-Time Market Adaptation

Monitor current market conditions:

```python
def adapt_to_market_regime(historical_data):
    """
    Detect market regime and adjust strategy accordingly.
    """
    volatility = calculate_current_volatility(historical_data)
    trend = detect_trend(historical_data)

    if volatility > HIGH_VOLATILITY_THRESHOLD:
        # High volatility: wider ranges, less concentration
        return "conservative"
    elif trend == "strong_trending":
        # Trending market: asymmetric ranges
        return "trending"
    else:
        # Normal market: balanced ranges
        return "balanced"
```

### 4. Hyperparameter Optimization

Use automated optimization:

```python
from scipy.optimize import minimize

def objective_function(params, historical_data):
    """
    Optimize strategy parameters using historical performance.
    """
    tick_lower, tick_upper, allocation_ratio = params

    # Simulate strategy with these parameters
    performance = backtest_with_params(
        tick_lower, tick_upper, allocation_ratio, historical_data
    )

    # Return negative (we minimize, but want to maximize performance)
    return -performance

# Optimize
result = minimize(
    objective_function,
    x0=[initial_lower, initial_upper, 0.5],
    args=(historical_data,),
    method='Nelder-Mead'
)

optimal_params = result.x
```

### 5. Risk Management

Balance performance with risk:

```python
def calculate_risk_adjusted_score(strategy, historical_data):
    """
    Score strategy based on risk-adjusted returns.
    """
    returns = simulate_returns(strategy, historical_data)

    # Sharpe ratio
    sharpe = np.mean(returns) / np.std(returns)

    # Maximum drawdown
    max_dd = calculate_max_drawdown(returns)

    # Calmar ratio
    calmar = np.mean(returns) / abs(max_dd)

    # Combined risk-adjusted score
    return 0.5 * sharpe + 0.5 * calmar
```

### 6. Competitive Intelligence

Analyze what works:

- **Monitor winning strategies**: If validators publish winning strategies, analyze them
- **Track your performance**: Keep detailed logs of which strategies scored best
- **A/B testing**: Try variations and measure improvements
- **Stay updated**: Follow DeFi research, new LP strategies, market microstructure papers

### 7. Infrastructure Optimization

Speed matters in competitive environments:

```bash
# Use faster database queries
# - Add indexes to frequently queried columns
# - Cache common calculations
# - Pre-compute features when possible

# Optimize code performance
# - Profile your code: python -m cProfile miner.py
# - Use vectorized operations (NumPy)
# - Parallelize independent calculations
# - Consider Rust/C++ extensions for critical paths

# Reduce latency
# - Host miner close to validators geographically
# - Use connection pooling for database
# - Implement request caching
# - Pre-load models into memory
```

### 8. Continuous Learning

Implement online learning:

```python
class OnlineLearningStrategy:
    def __init__(self):
        self.model = initialize_model()
        self.performance_history = []

    def update_model(self, recent_performance):
        """
        Update model based on recent performance.
        """
        # Retrain on recent data
        self.model = self.retrain_with_recent_data(recent_performance)

        # Adjust hyperparameters based on performance
        if performance_declining():
            self.adjust_hyperparameters()

    def generate_strategy(self, request):
        # Use updated model
        return self.model.predict(request)
```

### 9. Multi-Pool Expertise

If the subnet expands to multiple pools:

```python
class MultiPoolStrategy:
    def __init__(self):
        # Specialized strategies per pool
        self.pool_strategies = {
            "WETH_USDC": WethUsdcStrategy(),
            "WETH_DAI": WethDaiStrategy(),
            # ... more pools
        }

    def generate_strategy(self, request):
        pool_type = identify_pool_type(request.pairAddress)
        return self.pool_strategies[pool_type].generate_strategy(request)
```

### 10. Edge Cases and Robustness

Handle edge cases gracefully:

```python
def robust_strategy_generation(request):
    """
    Handle edge cases and ensure robustness.
    """
    try:
        # Validate input
        if not validate_request(request):
            return fallback_strategy(request)

        # Check for extreme market conditions
        if detect_extreme_conditions(request):
            return conservative_strategy(request)

        # Generate normal strategy
        strategy = generate_optimal_strategy(request)

        # Validate output
        if not validate_constraints(strategy, request.metadata.constraints):
            return adjust_to_meet_constraints(strategy, request)

        return strategy

    except Exception as e:
        log_error(e)
        # Always return valid strategy, even if not optimal
        return safe_fallback_strategy(request)
```

---

## Monitoring and Optimization

### Performance Tracking

```bash
# Create monitoring script
cat > ~/forever-money/monitor_performance.sh << 'EOF'
#!/bin/bash

# Check if miner is running
if ! pgrep -f "miner.miner" > /dev/null; then
    echo "[$(date)] ERROR: Miner not running!" | tee -a ~/miner_monitor.log
    sudo systemctl restart sn98-miner
fi

# Check endpoint health
RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health)
if [ "$RESPONSE" != "200" ]; then
    echo "[$(date)] ERROR: Miner endpoint unhealthy (HTTP $RESPONSE)" | tee -a ~/miner_monitor.log
    sudo systemctl restart sn98-miner
fi

# Log current status
echo "[$(date)] Miner running, endpoint healthy" >> ~/miner_monitor.log
EOF

chmod +x ~/forever-money/monitor_performance.sh

# Add to crontab (runs every 5 minutes)
crontab -e
# Add line:
# */5 * * * * /home/miner/forever-money/monitor_performance.sh
```

### Bittensor Network Monitoring

```bash
# Check your miner status
btcli wallet overview --wallet.name miner_wallet --netuid 98

# View your current weights/scores
btcli subnet list --netuid 98 | grep $(btcli wallet overview --wallet.name miner_wallet | grep "Hotkey" | awk '{print $2}')

# Check emissions
btcli wallet overview --wallet.name miner_wallet --netuid 98 | grep "Emission"

# Monitor metagraph
btcli subnet metagraph --netuid 98
```

### Application Performance Monitoring

```python
# Add performance tracking to your miner
import time
import logging
from functools import wraps

def track_performance(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        duration = time.time() - start

        logging.info(f"{func.__name__} took {duration:.2f}s")

        # Alert if too slow
        if duration > 30:  # 30 second threshold
            logging.warning(f"{func.__name__} is slow: {duration:.2f}s")

        return result
    return wrapper

@track_performance
def generate_strategy(request):
    # Your strategy code
    pass
```

### Resource Monitoring

```bash
# CPU and Memory
htop

# Disk usage
df -h

# Network
iftop

# Python process details
ps aux | grep python | grep miner

# Memory usage of miner
ps -o pid,user,%mem,command ax | grep miner
```

### Log Analysis

```bash
# View recent logs
tail -f ~/forever-money/miner.log

# Search for errors
grep ERROR ~/forever-money/miner.log

# Count requests per hour
grep "POST /predict_strategy" ~/forever-money/miner.log | awk '{print $1}' | sort | uniq -c

# Average response time
grep "took" ~/forever-money/miner.log | awk '{print $(NF-1)}' | awk '{sum+=$1; count++} END {print sum/count}'
```

### Automated Alerts

```bash
# Set up email alerts for issues
sudo apt install -y mailutils

# Create alert script
cat > ~/forever-money/alert.sh << 'EOF'
#!/bin/bash

# Check error rate
ERROR_COUNT=$(grep ERROR ~/forever-money/miner.log | tail -100 | wc -l)
if [ $ERROR_COUNT -gt 10 ]; then
    echo "High error rate: $ERROR_COUNT errors in last 100 logs" | mail -s "SN98 Miner Alert" your@email.com
fi

# Check response time
AVG_RESPONSE=$(grep "took" ~/forever-money/miner.log | tail -50 | awk '{print $(NF-1)}' | awk '{sum+=$1; count++} END {print sum/count}')
if (( $(echo "$AVG_RESPONSE > 10" | bc -l) )); then
    echo "Slow response time: ${AVG_RESPONSE}s average" | mail -s "SN98 Miner Performance Alert" your@email.com
fi
EOF

chmod +x ~/forever-money/alert.sh

# Add to crontab (runs every hour)
# 0 * * * * /home/miner/forever-money/alert.sh
```

### Strategy Performance Tracking

Create a performance database:

```python
import sqlite3
from datetime import datetime

class PerformanceTracker:
    def __init__(self, db_path="performance.db"):
        self.conn = sqlite3.connect(db_path)
        self.create_tables()

    def create_tables(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS strategy_performance (
                id INTEGER PRIMARY KEY,
                timestamp TEXT,
                round_id TEXT,
                strategy_type TEXT,
                score REAL,
                rank INTEGER,
                pnl_vs_hodl REAL,
                fees_generated REAL,
                max_il REAL
            )
        """)

    def log_performance(self, round_id, strategy_type, score, rank, details):
        self.conn.execute("""
            INSERT INTO strategy_performance
            (timestamp, round_id, strategy_type, score, rank, pnl_vs_hodl, fees_generated, max_il)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now().isoformat(),
            round_id,
            strategy_type,
            score,
            rank,
            details.get('pnl_vs_hodl'),
            details.get('fees_generated'),
            details.get('max_il')
        ))
        self.conn.commit()

    def get_best_strategies(self, limit=10):
        cursor = self.conn.execute("""
            SELECT strategy_type, AVG(score) as avg_score, COUNT(*) as count
            FROM strategy_performance
            GROUP BY strategy_type
            ORDER BY avg_score DESC
            LIMIT ?
        """, (limit,))
        return cursor.fetchall()
```

---

## Troubleshooting

### Issue 1: Miner Not Receiving Requests

**Symptoms:** No requests from validators

**Solutions:**
```bash
# Check if miner is reachable
curl -X POST http://YOUR_PUBLIC_IP:8000/predict_strategy -H "Content-Type: application/json" -d '{}'

# Check firewall
sudo ufw status
sudo ufw allow 8000/tcp

# Check if port is open
sudo netstat -tulpn | grep 8000

# Verify registration
btcli wallet overview --wallet.name miner_wallet --netuid 98

# Check metagraph for your axon info
python3 << 'EOF'
import bittensor as bt
sub = bt.subtensor('finney')
meta = sub.metagraph(98)
# Find your UID and check axon info
EOF
```

### Issue 2: Database Connection Errors

**Symptoms:** Cannot connect to Postgres database

**Solutions:**
```bash
# Test database credentials from request
psql -h <HOST> -p 5432 -U <USER> -d <DATABASE>

# Check network connectivity
ping <POSTGRES_HOST>
telnet <POSTGRES_HOST> 5432

# Verify PostgreSQL client installed
psql --version

# Test with Python
python3 << 'EOF'
import psycopg2
conn = psycopg2.connect(
    host="<HOST>",
    port=5432,
    database="<DATABASE>",
    user="<USER>",
    password="<PASSWORD>"
)
print("Connected successfully!")
conn.close()
EOF
```

### Issue 3: Constraint Violations (Zero Score)

**Symptoms:** Always receiving score of 0

**Solutions:**
```bash
# Review validator constraints
# Check your logs for constraint details

# Validate your strategy locally
python3 << 'EOF'
from validator.constraints import ConstraintValidator

validator = ConstraintValidator()
result = validator.validate(your_strategy, constraints)
if not result.valid:
    print(f"Violations: {result.violations}")
EOF

# Common issues:
# - Tick width too narrow (check min_tick_width)
# - IL exceeds limit (check max_il)
# - Too many rebalances (check max_rebalances)
# - Allocations exceed inventory
```

### Issue 4: Slow Response Times

**Symptoms:** Requests timing out or taking too long

**Solutions:**
```bash
# Profile your code
python -m cProfile -o profile.stats miner.py

# Analyze profile
python -m pstats profile.stats
# Then: sort cumulative
#       stats 20

# Common bottlenecks:
# - Database queries: Add indexes, use connection pooling
# - Model inference: Use batch processing, GPU acceleration
# - Data processing: Use NumPy vectorization

# Optimize database queries
# - Use LIMIT to restrict result size
# - Add WHERE clauses for block ranges
# - Create indexes on frequently queried columns

# Increase worker processes
# Edit systemd service: -w 8 instead of -w 4

# Use caching
pip install flask-caching
```

### Issue 5: Out of Memory

**Symptoms:** Process killed, OOM errors

**Solutions:**
```bash
# Check memory usage
free -h

# Monitor Python memory
pip install memory_profiler
python -m memory_profiler miner.py

# Add swap space
sudo fallocate -l 8G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# Optimize code:
# - Don't load entire dataset into memory
# - Use generators instead of lists
# - Clear cache periodically
# - Use smaller batch sizes for ML models

# Upgrade server RAM if necessary
```

### Issue 6: Poor Strategy Performance

**Symptoms:** Low scores compared to other miners

**Solutions:**
```bash
# Backtest your strategy locally
python3 << 'EOF'
from validator.backtester import Backtester

# Compare your strategy to simple baselines
your_result = backtest(your_strategy)
baseline_result = backtest(simple_strategy)

print(f"Your PnL: {your_result.pnl_vs_hodl}%")
print(f"Baseline PnL: {baseline_result.pnl_vs_hodl}%")
EOF

# Common issues:
# - Overfitting to historical data
# - Not adapting to current market conditions
# - Suboptimal tick ranges
# - Poor rebalancing logic

# Solutions:
# - Use cross-validation on historical data
# - Implement regime detection
# - Test on out-of-sample data
# - Analyze winning strategies (if available)
```

### Issue 7: High Server Costs

**Symptoms:** Cloud bills too high for rewards

**Solutions:**
```bash
# Optimize compute costs:
# - Use spot/preemptible instances (50-90% cheaper)
# - Right-size your instance (don't over-provision)
# - Consider cheaper providers (Hetzner, DigitalOcean vs AWS/GCP)

# AWS Spot Instance example:
aws ec2 request-spot-instances \
  --spot-price "0.05" \
  --instance-count 1 \
  --type "one-time" \
  --launch-specification file://specification.json

# Use serverless for bursty workloads
# - Lambda/Cloud Functions for inference
# - Only pay for actual requests

# Optimize data transfer:
# - Cache historical data locally
# - Minimize external API calls
# - Compress responses
```

### Issue 8: Model Not Improving

**Symptoms:** Strategy performance plateaued

**Solutions:**
```python
# Implement systematic experimentation
class ExperimentTracker:
    def run_experiment(self, strategy_variant, historical_data):
        # Test variant
        performance = self.backtest(strategy_variant, historical_data)

        # Compare to baseline
        if performance > self.baseline_performance * 1.05:
            print(f"Improvement found! {performance - self.baseline_performance}%")
            self.baseline_strategy = strategy_variant
            self.baseline_performance = performance

        # Log experiment
        self.log_experiment(strategy_variant, performance)

# Try different approaches:
# - Different features
# - Different models (LSTM vs Transformer vs GRU)
# - Different hyperparameters
# - Different ensemble weights
# - Different risk tolerance
```

### Getting Help

If you're still experiencing issues:

1. **GitHub Issues:** [AuditBase/forever-money/issues](https://github.com/AuditBase/forever-money/issues)
2. **Documentation:** Review README.md, spec.md, and CLAUDE.md
3. **Bittensor Discord:** Join for community support
4. **Validator Logs:** If available, check validator logs for error messages
5. **Community:** Connect with other miners to share (non-sensitive) insights

---

## Security Best Practices

### 1. Protect Your Keys

```bash
# Never share your mnemonic phrase
# Store securely encrypted

# Restrict wallet file permissions
chmod 600 ~/.bittensor/wallets/*/coldkey
chmod 600 ~/.bittensor/wallets/*/hotkeys/*

# Use separate hotkeys for different activities
btcli wallet new_hotkey --wallet.name miner_wallet --wallet.hotkey backup_hotkey
```

### 2. Secure Your Server

```bash
# Enable firewall
sudo ufw enable
sudo ufw allow 22/tcp  # SSH
sudo ufw allow 8000/tcp  # Miner endpoint

# Disable root SSH
sudo sed -i 's/PermitRootLogin yes/PermitRootLogin no/' /etc/ssh/sshd_config
sudo systemctl restart sshd

# Use SSH keys only
sudo sed -i 's/PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo systemctl restart sshd

# Install fail2ban
sudo apt install -y fail2ban
sudo systemctl enable fail2ban
sudo systemctl start fail2ban

# Keep system updated
sudo apt update && sudo apt upgrade -y

# Enable automatic security updates
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure --priority=low unattended-upgrades
```

### 3. Protect Your Code

```bash
# If you have proprietary strategies:

# Use environment variables for sensitive config
# NEVER commit secrets to git

# Add to .gitignore:
echo ".env" >> .gitignore
echo "*.pth" >> .gitignore  # ML model weights
echo "*.pkl" >> .gitignore  # Pickle files
echo "performance.db" >> .gitignore

# Use private repository for custom code
git remote add origin git@github.com:yourusername/private-sn98-strategies.git

# Encrypt sensitive files
gpg --encrypt --recipient your@email.com models/secret_model.pth
```

### 4. Rate Limiting and DDoS Protection

```python
# Add rate limiting to your miner
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(
    app,
    key_func=get_remote_address,
    default_limits=["100 per hour"]
)

@app.route('/predict_strategy', methods=['POST'])
@limiter.limit("10 per minute")  # Max 10 requests per minute per IP
def predict_strategy():
    # Your code
    pass
```

### 5. Input Validation

```python
def validate_request(request_data):
    """
    Validate all inputs to prevent injection attacks.
    """
    # Check required fields
    required = ['pairAddress', 'chainId', 'target_block', 'metadata']
    for field in required:
        if field not in request_data:
            raise ValueError(f"Missing required field: {field}")

    # Validate types
    if not isinstance(request_data['chainId'], int):
        raise ValueError("chainId must be integer")

    if not isinstance(request_data['target_block'], int):
        raise ValueError("target_block must be integer")

    # Validate ranges
    if request_data['chainId'] != 8453:
        raise ValueError("Only Base (8453) supported")

    if request_data['target_block'] < 0:
        raise ValueError("Invalid block number")

    # Validate address format
    if not request_data['pairAddress'].startswith('0x'):
        raise ValueError("Invalid address format")

    return True
```

### 6. Monitoring and Alerting

```bash
# Set up intrusion detection
sudo apt install -y aide
sudo aideinit
sudo mv /var/lib/aide/aide.db.new /var/lib/aide/aide.db

# Check for intrusions daily
sudo aide --check

# Monitor logs for suspicious activity
sudo apt install -y logwatch
sudo logwatch --detail High --mailto your@email.com --range today
```

---

## Advanced Topics

### Multi-Region Deployment

For high availability:

```bash
# Deploy miners in multiple regions
# - US East
# - US West
# - Europe
# - Asia

# Use DNS load balancing or provide multiple endpoints
# Validators can try multiple endpoints if one fails
```

### Continuous Integration/Deployment

Automate testing and deployment:

```yaml
# .github/workflows/deploy.yml
name: Deploy Miner

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2

      - name: Run tests
        run: |
          python -m pytest tests/

      - name: Deploy to server
        run: |
          ssh miner@your-server "cd forever-money && git pull && systemctl restart sn98-miner"
```

### Model Versioning

Track model versions:

```python
class VersionedModel:
    def __init__(self):
        self.version = "2.1.0"
        self.model_path = f"models/strategy_v{self.version}.pth"
        self.load_model()

    def load_model(self):
        if os.path.exists(self.model_path):
            self.model.load_state_dict(torch.load(self.model_path))
        else:
            logging.warning(f"Model {self.version} not found, using default")

    def save_model(self):
        torch.save(self.model.state_dict(), self.model_path)
        # Also save to versioned backup
        backup_path = f"models/backup/strategy_v{self.version}_{datetime.now().strftime('%Y%m%d')}.pth"
        torch.save(self.model.state_dict(), backup_path)
```

---

## FAQ

**Q: How much can I earn as a miner?**
A: Earnings depend on your strategy performance and stake. Top 3 miners receive most rewards due to the 70% performance component being top-heavy weighted.

**Q: Do I need to provide liquidity to compete?**
A: No. The 70% performance component doesn't require liquidity. The 30% LP alignment component does, but you can still compete with just strategy quality.

**Q: How often are rounds?**
A: This depends on validator configuration. Expect anywhere from hourly to daily rounds.

**Q: Can I run multiple miners?**
A: Yes, but each requires separate registration and stake. Consider if the additional cost is worth it.

**Q: What programming languages can I use?**
A: The reference implementation is Python, but you can use any language as long as you expose an HTTP endpoint with the correct JSON format.

**Q: How do I know if my strategy is good before deploying?**
A: Backtest thoroughly using the provided historical data. Compare against simple baselines (e.g., full-range position, 50-50 split).

**Q: Can I see other miners' strategies?**
A: No, strategies are private. Only the winning strategy may be published by validators.

**Q: What happens if my miner goes offline?**
A: You'll receive zero score for that round. Ensure high uptime with proper monitoring and auto-restart.

**Q: How do I upgrade my strategy without downtime?**
A: Use blue-green deployment: run new version on different port, test, then switch. Or use rolling restart with systemd.

**Q: What's the minimum viable strategy?**
A: At minimum: accept requests, query database, return valid positions that meet constraints. Even a simple rule-based approach can earn some rewards.

---

## Conclusion

You now have everything you need to become a competitive SN98 miner! Remember:

- **Strategy quality is paramount** - only top 3 get full weight
- **Constraint compliance is critical** - violations = zero score
- **Continuous improvement is essential** - other miners will adapt
- **Backtesting is your friend** - test thoroughly before deploying
- **Monitor everything** - uptime, performance, scores, errors

### Next Steps

1. ✅ Complete server setup and registration
2. ✅ Deploy basic miner to verify connectivity
3. ✅ Implement your first strategy (start simple)
4. ✅ Backtest thoroughly on historical data
5. ✅ Deploy and monitor performance
6. ✅ Iterate and improve based on results
7. ✅ Join the community and stay updated

### Resources

- **Documentation:**
  - [README.md](README.md) - General project information
  - [VALIDATOR_SETUP.md](VALIDATOR_SETUP.md) - Validator setup guide
  - [spec.md](spec.md) - Technical specification
  - [CLAUDE.md](CLAUDE.md) - Development guidelines

- **Support:**
  - [GitHub Issues](https://github.com/AuditBase/forever-money/issues)
  - Bittensor Discord
  - SN98 community channels

- **Learning Resources:**
  - Uniswap v3 whitepaper (understand tick ranges and concentrated liquidity)
  - Aerodrome documentation
  - DeFi research papers on LP strategies
  - Bittensor documentation

Good luck, and may your strategies be profitable! 🚀

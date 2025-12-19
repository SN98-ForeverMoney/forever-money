# SN98 ForeverMoney

**Decentralized Automated Liquidity Management on Bittensor**

## Quick Summary

SN98 ForeverMoney is a Bittensor subnet that optimizes Uniswap V3 / Aerodrome liquidity provision through competitive AI strategies. Miners propose dynamic rebalancing decisions, validators evaluate performance through forward simulations, and winning strategies get executed on-chain on Base L2.

**Key Features:**
- **Jobs-Based Architecture** - Multiple liquidity pools managed concurrently
- **Dual-Mode Operation** - Evaluation rounds (all miners) + Live rounds (winners only)
- **Rebalance-Only Protocol** - Miners decide when and how to adjust positions
- **Per-Job Reputation** - Miners build scores for specific trading pairs
- **7-Day Participation Requirement** - Consistent performance needed for live execution

## How It Works

Validators run multiple jobs (liquidity management tasks) concurrently. For each job:

1. **Evaluation Rounds** - All miners compete in forward simulations from current blockchain state
2. **Live Rounds** - Winning miners (after 7 days participation) execute strategies on-chain
3. **Scoring** - Miners scored on portfolio value, 50/50 balance maintenance, and fees
4. **Reputation** - Build per-job scores through exponential moving averages

**Current Scoring (PoL Target):**
- Maintain 50/50 token balance (critical!)
- Preserve capital (minimize impermanent loss)
- Collect fees (secondary, 10% weight)

For detailed system architecture, round flows, and database design, see **[ARCHITECTURE.md](./ARCHITECTURE.md)**.

## For Miners

**Getting Started:** Implement a `rebalance_query_handler` that responds to `RebalanceQuery` requests from validators. Accept/refuse jobs and return desired positions (rebalance or keep current). Build reputation through consistent participation for 7 days to become eligible for live execution.

**Run Your Miner:**
```bash
python -m miner.miner --wallet.name <wallet> --wallet.hotkey <hotkey>
```

For complete implementation guide, scoring details, and code examples, see **[MINER_GUIDE.md](./MINER_GUIDE.md)**.

## Documentation

### Core Documentation
- **[ARCHITECTURE.md](./ARCHITECTURE.md)** - Complete system architecture, round flows, database design
- **[MINER_GUIDE.md](./MINER_GUIDE.md)** - Comprehensive miner implementation guide with scoring details

## Network Information

- **Subnet ID**: 98
- **Network**: Bittensor Finney (mainnet)
- **Protocol**: Uniswap V3 / Aerodrome
- **Round Duration**: 15 minutes (configurable per job)
- **Live Eligibility**: 7 days participation

## Development

### Requirements
- Python 3.10+
- Bittensor wallet

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

TBD
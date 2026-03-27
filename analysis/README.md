# Analysis Scripts

On-chain analysis of Aerodrome CL liquidity positions on Base. Fetches Mint/Burn/Collect/Swap events via RPC, builds position timelines, and generates interactive HTML dashboards.

## Scripts

| Script               | Pool       | Purpose                                                                                            | Output                         |
| -------------------- | ---------- | -------------------------------------------------------------------------------------------------- | ------------------------------ |
| `vault_analysis.py`  | WETH/BID   | Multi-vault comparison (ForeverMoney vs Arrakis vs Arcadia vs vfat)                                | `output/vaults_dashboard.html` |
| `chutes_analysis.py` | xSN64/USDC | Single-vault capital efficiency analysis                                                           | `output/chutes_dashboard.html` |
| `common.py`          | —          | Shared infrastructure (RPC fetching, Blockscout API, event processing, caching, timeline builders) | —                              |

## Usage

```bash
# Run from cached data (fast, ~30s for timestamp lookups):
python analysis/vault_analysis.py
python analysis/chutes_analysis.py

# Fresh fetch (needs RPC key from blockchain providers):
BASE_RPC="" python analysis/vault_analysis.py
```

## Data

- `data/vaults_pool_events.json` — cached WETH/BID pool events
- `data/chutes_pool_events.json` — cached xSN64/USDC pool events

Delete a cache file to force re-fetch on next run (requires `BASE_RPC`).

# Miner Performance Report Script

## Context

Miner operators need a consolidated view of their vault performance — positions, values, fees, rebalance activity, PnL, and HODL comparison. This script (`scripts/miner_report.py`) is an on-demand CLI tool that reads on-chain state (always available) and optionally enriches with database metrics.

## Architecture: Two-Tier Data Collection

### Tier 1: On-Chain (always available, requires only `BASE_RPC`)

- Current positions (tick ranges, allocated amounts)
- Unallocated inventory (tokens not deployed into positions)
- Current pool price and tick
- Token symbols, decimals (ERC20 calls)
- USD token prices (CoinGecko/GeckoTerminal via existing `PriceService`)
- **Uncollected fees** (via static call to `AeroCLPositionManager.claimFees()` with `from=vault_address`)

### Tier 2: Database (optional, requires `JOBS_POSTGRES_*` env vars)

- Fees collected (from `base_poolcl_collects_v2` table, filtered by position manager owner)
- Rebalance count (from `base_poolcl_mints_v2` table, filtered by position manager owner)
- PnL calculation (from `base_poolcl_mints_v2`, `base_poolcl_burns_v2`, `base_poolcl_collects_v2`)
- **HODL comparison** — reconstructs starting token balances from mint/collect events, compares to current vault value
- DB connection built from `JOBS_POSTGRES_*` env vars with SSL support
- Uses Tortoise ORM with `validator.models.pool_events` models

## Metrics in the Report

| Metric                                                     | Source                                              | Tier |
| ---------------------------------------------------------- | --------------------------------------------------- | ---- |
| Current price (tick + human-readable)                      | `SnLiqManagerService.get_current_price()`           | 1    |
| Active positions (tick range, price range, width)          | `SnLiqManagerService.get_current_positions()`       | 1    |
| Position in-range status                                   | Compare current_tick to [tick_lower, tick_upper]    | 1    |
| Token allocations per position (human + USD)               | Position amounts + `PriceService.get_token_price()` | 1    |
| Unallocated inventory (human + USD)                        | `SnLiqManagerService.get_inventory()`               | 1    |
| Total vault value (deployed + unallocated, USD)            | Sum of above                                        | 1    |
| Uncollected fees (token amounts + USD)                     | `AeroCLPositionManager.claimFees()` static call     | 1    |
| Fees collected (token amounts + USD, over lookback period) | `CollectEvent` aggregation by owner                 | 2    |
| Number of fee collections                                  | `CollectEvent` count                                | 2    |
| Number of rebalances                                       | `MintEvent` count by owner                          | 2    |
| PnL per token (burns + fees - mints, over lookback period) | `MintEvent` + `BurnEvent` + `CollectEvent`          | 2    |
| Approximate PnL in USD (using current prices)              | PnL tokens \* current USD price                     | 2    |
| HODL value (starting tokens at current prices)             | Reconstructed from `MintEvent` + `CollectEvent`     | 2    |
| LP value (current vault tokens at current prices)          | Positions + unallocated + uncollected fees          | 2    |
| LP vs HODL delta (positive = LP outperformed)              | LP value - HODL value                               | 2    |

## PnL Calculation

PnL is calculated in **token-denominated terms** over the lookback period:

```
tokens_in  = sum(amount0/amount1 from MintEvents)   # deposited into positions
tokens_out = sum(amount0/amount1 from BurnEvents)    # withdrawn from positions
           + sum(amount0/amount1 from CollectEvents)  # fees collected

PnL (per token) = tokens_out - tokens_in
```

USD PnL is approximate — it uses **current** token prices, not historical prices at the time of each event. This is clearly labeled in the output.

All events are filtered by `owner == position_manager_address` (resolved via `akAddressToPositionManager`).

## HODL Comparison (Tier 2, DB-based)

Answers: **"Would we have been better off just holding the tokens instead of LP'ing?"**

### Why DB-only?

A real HODL comparison requires knowing the vault's **starting token balances**. On-chain state (Tier 1) only shows the current snapshot — it can't tell us what the vault held before LP activity. The uncollected fees alone do NOT represent a valid HODL comparison because they ignore impermanent loss (IL). A vault could earn $10 in fees but suffer $15 in IL, meaning LP actually underperformed HODL by $5.

With DB events, we reconstruct starting balances precisely:

```
net_flow_per_token = sum(collects) - sum(mints)           # actual token transfers in/out
starting_tokens    = current_vault_tokens - net_flow       # reconstruct initial state
current_vault_tokens = positions + unallocated + uncollected_fees

hodl_value = starting_token0 * current_price0_usd + starting_token1 * current_price1_usd
lp_value   = current_token0 * current_price0_usd + current_token1 * current_price1_usd
delta      = lp_value - hodl_value    # positive = LP outperformed HODL
```

The delta captures both IL (negative) and fee income (positive) in one number.

### DB dependency

The HODL comparison relies entirely on mint/collect/burn events being indexed in the pool events database. For newly deployed vaults with no events yet (e.g., `0x88c6...`), the HODL section will not appear — same limitation as Tier 2 fees and PnL. Data populates automatically once rebalances or fee collections are triggered and indexed.

## Environment Variables (loaded via dotenv from `.env`)

| Env Var                  | Required                | Purpose                                                               |
| ------------------------ | ----------------------- | --------------------------------------------------------------------- |
| `MINER_VAULT_ADDRESSES`  | Yes (or `--vaults` CLI) | JSON array of vault addresses                                         |
| `BASE_RPC`               | Yes                     | Base L2 RPC endpoint                                                  |
| `MINER_VAULT_CHAIN_ID`   | No (default 8453)       | Chain ID                                                              |
| `JOBS_POSTGRES_HOST`     | No                      | Postgres host. When set (along with DB/USER), enables Tier 2 metrics. |
| `JOBS_POSTGRES_PORT`     | No (default 5432)       | Postgres port                                                         |
| `JOBS_POSTGRES_DB`       | No (default postgres)   | Postgres database name                                                |
| `JOBS_POSTGRES_USER`     | No                      | Postgres user                                                         |
| `JOBS_POSTGRES_PASSWORD` | No                      | Postgres password                                                     |

No API keys needed for CoinGecko — uses the free public endpoint.

## CLI Interface

```bash
python scripts/miner_report/miner_report.py                       # reads all config from .env
python scripts/miner_report/miner_report.py --vaults 0xABC,0xDEF  # override vault addresses
python scripts/miner_report/miner_report.py --json                 # JSON output
python scripts/miner_report/miner_report.py --no-db                # skip database queries (Tier 2)
python scripts/miner_report/miner_report.py --lookback-days 7      # fee/PnL lookback (default 30)
python scripts/miner_report/miner_report.py --verbose              # enable debug logging
```

## Services Reused

- `miner/volatility_miner.py:discover_vault_pool()` — pool discovery from vault address
- `validator/services/liqmanager.py:SnLiqManagerService` — on-chain state (price, positions, inventory, pool tokens)
- `validator/services/price.py:PriceService.get_token_price()` — USD prices via CoinGecko
- `validator/utils/math.py:UniswapV3Math` — `sqrt_price_x96_to_price()`, `get_sqrt_ratio_at_tick()`
- `validator/utils/web3.py:AsyncWeb3Helper` — ERC20 contract calls (symbol, decimals), `AeroCLPositionManager` contract for `claimFees()`
- `validator/repositories/pool.py:PoolDataDB` — fee/event queries (`get_collect_events`, `get_mint_events`, `get_burn_events`)
- `validator/models/pool_events.py` — Tortoise ORM models (`CollectEvent`, `MintEvent`, `BurnEvent`)

## Implementation Steps

### 1. CLI setup + imports

- `load_dotenv()` at top of script to load `.env` (same pattern as `validator/utils/env.py`)
- argparse for `--vaults` (override), `--chain-id`, `--json`, `--no-db`, `--lookback-days`, `--verbose`
- Vault addresses: CLI `--vaults` flag falls back to `MINER_VAULT_ADDRESSES` from `.env`
- DB connection: built from `JOBS_POSTGRES_*` env vars with SSL context (not a CLI arg)
- sys.path setup to import from project root

### 2. Data classes

- `PositionReport`: tick_lower, tick_upper, price_lower, price_upper, width_ticks, allocation0/1 (human + USD), is_in_range
- `DbMetrics`: fee0/1 (human + USD), total_fees_usd, collection_count, rebalance_count, pnl0/1 (human + USD), total_pnl_usd, hodl_value_usd, lp_value_usd, hodl_delta_usd
- `VaultReport`: vault/pool addresses, token symbols/decimals, current price/tick, unallocated inventory, positions list, total values, uncollected fees, optional DbMetrics
- `ReportParams`: lookback_days, db_enabled, vault_source
- `MinerReport`: timestamp, chain_id, params, list of VaultReports, totals (value, uncollected fees, historical fees, PnL, HODL delta)

### 3. Helper: `get_token_info(chain_id, token_address) -> (symbol, decimals)`

- ERC20 calls via `AsyncWeb3Helper.make_web3(chain_id).make_contract_by_name("ERC20", addr)`
- `symbol()` and `decimals()` called concurrently

### 4. Core: `build_vault_report(vault_address, chain_id, include_db, lookback_days) -> VaultReport`

1. `discover_vault_pool(vault_address, chain_id)` → pool_address
2. Create `SnLiqManagerService(chain_id, vault_address, pool_address)`
3. Gather concurrently: current_price, tick_spacing, positions, inventory, pool_tokens
4. Get token info (symbol, decimals) for both tokens
5. Get USD prices for both tokens via `PriceService.get_token_price()` (graceful fallback)
6. Convert wei amounts → human-readable using decimals
7. Convert ticks → prices using `UniswapV3Math.sqrt_price_x96_to_price(get_sqrt_ratio_at_tick(tick), dec0, dec1)`
8. Calculate total deployed USD, unallocated USD, total vault value
9. Resolve position manager via `resolve_position_manager()` (handles reverts gracefully)
10. Tier 1: static call to `claimFees()` on position manager (with `from=vault_address`) for uncollected fees
11. If `include_db`: compute total vault tokens (positions + unallocated + uncollected fees), pass to `collect_db_metrics()` for historical fees/rebalances/PnL/HODL

### 5. DB metrics: `collect_db_metrics(position_manager, ..., current_total0_human, current_total1_human) -> DbMetrics`

1. Receive pre-resolved position manager address and current vault token totals (positions + unallocated + uncollected fees)
2. Calculate `start_block = current_block - (lookback_days * 43200)`
3. Fetch concurrently: `get_collect_events`, `get_mint_events`, `get_burn_events` (all from `PoolDataDB`)
4. Filter all events by `owner == position_manager_address` (lowercased, no 0x prefix)
5. Aggregate fees: sum `amount0`/`amount1` from collect events
6. Count rebalances: count of mint events
7. Calculate PnL:
    - `tokens_in` = sum of `amount0`/`amount1` from mint events
    - `tokens_out` = sum of `amount0`/`amount1` from burn events + fee totals
    - `pnl = tokens_out - tokens_in` (positive = profit)
8. HODL comparison (only when events exist in lookback window):
    - `net_flow = sum(collects) - sum(mints)` per token (actual transfers)
    - `starting_tokens = current_vault_tokens - net_flow`
    - `hodl_value = starting_tokens * current_prices`
    - `lp_value = current_vault_tokens * current_prices`
    - `hodl_delta = lp_value - hodl_value`
9. Convert to human-readable and approximate USD using current prices

### 6. Output formatting: `print_report(report: MinerReport)`

Console-friendly plain text with sections per vault:

```
========================================================================================================================
  SN98 Miner Vault Report
  Generated: 2026-03-18T12:00:00Z | Chain: Base (8453)
  Params: lookback=30d | db=on | vaults=3
========================================================================================================================

────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  Vault: 0x88C69a...
  Pool:  0x1024c2...
  Pair:  WETH / BID
  Price: 269,700.19 BID/WETH (tick 125056) | Tick Spacing: 200

  Unallocated Inventory:
    0.003570 WETH ($8.32)
    0.000000 BID ($0.00)

  Positions (1 total, 1 in range):
    [1] ticks [122800, 126800] → 215,213.54 - 321,054.46 BID/WETH
        Width: 4000 ticks | Status: IN RANGE
        0.019429 WETH ($45.31)
        6,699.5743 BID ($57.38)

  Value:
    Deployed: $102.69 | Unallocated: $8.32 | Total: $111.02

  Uncollected Fees:
    0.000018 WETH ($0.04) | 5.064191 BID ($0.04)
    Total: $0.08

  Fees (30d):
    0.005000 WETH ($11.65) | 500.0000 BID ($4.28)
    Total Fees: $15.93
    Collections: 8 | Rebalances: 12

  PnL (30d, approximate):
    WETH: +0.005000 ($11.65) | BID: -1,234.5600 (-$10.57)
    Net PnL: +$1.08

  HODL Comparison (30d, DB-based — requires indexed on-chain events):
    HODL Value: $110.00 | LP Value: $111.02
    LP vs HODL: +$1.02 (LP outperformed)

========================================================================================================================
  Portfolio Total: $111.02 | Uncollected Fees: $0.08 | Total Fees: $15.93 | Net PnL: +$1.08 | LP vs HODL: +$1.02
========================================================================================================================

```

### 7. Error handling

- Missing vault addresses → clear error + exit
- Pool discovery fails → warn, skip vault, continue
- RPC errors → catch per-vault, report error inline
- Price API fails → show amounts without USD, note "price unavailable"
- DB unavailable → Tier 2 fields (fees, PnL, HODL) not shown
- No DB events in lookback window → HODL comparison fields are `None` (section won't print)
- `claimFees()` reverts → uncollected fees default to 0/None, report continues

## Key Implementation Details

- **Position manager as owner**: The `owner` field in pool events is the **position manager** address (not the vault address). Resolved via `liq_manager.functions.akAddressToPositionManager(token)`.
- **PM resolution handles reverts**: `akAddressToPositionManager(token)` reverts (custom error `0x82b42900`) for unregistered tokens instead of returning `ZERO_ADDRESS`. Each call is wrapped in try/except via `_try_get_position_manager()`.
- **`claimFees()` requires `from=vault_address`**: The position manager has access control — only the vault (LiquidityManager) can call `claimFees()`. Pass `{"from": vault_address}` to `.call()` to set `msg.sender` in the simulation. The vault address IS the LiquidityManager address (confirmed via `pm_contract.functions.liquidityManager().call()`).
- **Address format in DB**: Addresses stored **without 0x prefix**, lowercased. Use `.lower().replace("0x", "")`.
- **Token decimals**: Queried on-chain per token, not hardcoded (e.g. USDC=6, WETH=18).
- **DB connection**: Built from `JOBS_POSTGRES_*` env vars. Uses SSL with `CERT_NONE` for remote connections (e.g. RDS).
- **Base blocks per day**: 43,200 (2s block time). Used to compute `start_block` from `lookback_days`.
- **PnL USD is approximate**: Uses current token prices, not historical. Labeled clearly in output.
- **HODL comparison is DB-only**: Requires mint/collect events to reconstruct starting balances. On-chain state alone cannot determine historical token composition. Uncollected fees alone are NOT a valid HODL proxy — they ignore impermanent loss.
- **Why DB over on-chain for events**: Fetching historical events from chain would require paginated `eth_getLogs` over ~1.3M blocks (30 days on Base), with most public RPCs limiting to ~10k blocks per request. The subgraph-indexed DB is orders of magnitude faster.

## Verification

1. **Without DB**: Run `python scripts/miner_report.py --no-db` — shows positions, inventory, USD values, and uncollected fees (Tier 1). No fees/PnL/HODL sections.
2. **With DB**: Run `python scripts/miner_report.py` — additionally shows historical fees, rebalances, PnL, and HODL comparison (Tier 2)
3. **JSON mode**: Run `python scripts/miner_report.py --json` — valid JSON with `uncollected*` fields, `db_metrics` (including `hodl_value_usd`, `lp_value_usd`, `hodl_delta_usd`), and `total_hodl_delta_usd`
4. **Multiple vaults**: All 3 vaults from `MINER_VAULT_ADDRESSES` should report independently
5. **Uncollected fees**: Should be non-zero for vaults with in-range positions
6. **PnL consistency**: Verify `pnl = (burns + fees) - mints` matches displayed values
7. **HODL comparison**: Positive delta = LP outperformed HODL; negative = HODL would have been better. Only appears when DB events exist in lookback window.
8. **New vaults**: HODL/PnL/fees show `None`/`$0` until first rebalance/collection is indexed in DB

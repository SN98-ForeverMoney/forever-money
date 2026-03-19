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
- **HODL vs Strategy PnL** — uses swap events for historical pool prices, V3 math for starting token composition, and CoinGecko `market_chart` for historical USD prices
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
| Start value (USD at historical prices)                     | V3 math + swap events + CoinGecko `market_chart`    | 2    |
| HODL value (starting tokens at current prices)             | Starting composition held to today                  | 2    |
| HODL PnL (USD + %)                                         | `hodl_value - start_value`                          | 2    |
| Strategy value (current deployed tokens at current prices) | Positions + uncollected fees at current prices      | 2    |
| Strategy PnL (USD + %)                                     | `strategy_value - start_value`                      | 2    |
| HODL delta (USD + %)                                       | `strategy_pnl - hodl_pnl` (positive = LP won)      | 2    |

## HODL vs Strategy PnL (Tier 2, DB-based)

Answers: **"How is the LP strategy performing vs simply holding the tokens?"**

### Approach

The old PnL (token-denominated: burns+fees-mints) and old HODL comparison have been replaced with a unified **HODL vs Strategy** comparison that uses historical USD prices for an accurate start value.

**Scope: deployed tokens only** — unallocated inventory is excluded. Uncollected fees count toward Strategy value (they are part of the LP position's earned value).

### Data Sources

1. **Starting token composition**: Determined from the pool price at the start point using Uniswap V3 math. The pool price comes from **swap events** in the DB (`get_sqrt_price_at_block`), and V3 math (`get_amounts_for_liquidity`) computes token0/token1 amounts from the position's tick range and the pool's sqrtPriceX96 at that block.
2. **Historical USD prices**: Fetched from CoinGecko's `market_chart` endpoint via `PriceService.get_historical_token_price(token_address, chain_id, target_timestamp)`. This gives accurate USD prices at the start point rather than using current prices as an approximation.
3. **Current USD prices**: From `PriceService.get_token_price()` as before (CoinGecko/GeckoTerminal).

### Hybrid Start Point

The start timestamp uses a **hybrid** approach:
- Default: lookback period start (e.g., 30 days ago)
- Clamped to position creation (first `MintEvent` block_time) for positions newer than the lookback window

This means the comparison window automatically adjusts for newer positions — you always compare from when the position was actually created, not from before it existed.

### Formula

```
# Start point
start_timestamp  = max(lookback_start, first_mint_block_time)   # hybrid clamp
start_sqrt_price = get_sqrt_price_at_block(pool, start_block)   # from swap events
start_token0, start_token1 = V3_math(position_ticks, start_sqrt_price, liquidity)
start_price0_usd = get_historical_token_price(token0, chain_id, start_timestamp)
start_price1_usd = get_historical_token_price(token1, chain_id, start_timestamp)
start_value_usd  = start_token0 * start_price0_usd + start_token1 * start_price1_usd

# HODL: what if we just held the starting tokens?
hodl_value_usd = start_token0 * current_price0_usd + start_token1 * current_price1_usd
hodl_pnl_usd   = hodl_value_usd - start_value_usd
hodl_pnl_pct   = hodl_pnl_usd / start_value_usd * 100

# Strategy: actual LP performance (deployed tokens + uncollected fees)
strategy_value_usd = current_deployed_token0 * current_price0_usd + current_deployed_token1 * current_price1_usd
strategy_pnl_usd   = strategy_value_usd - start_value_usd
strategy_pnl_pct   = strategy_pnl_usd / start_value_usd * 100

# Delta: did LP beat HODL?
hodl_delta_usd = strategy_pnl_usd - hodl_pnl_usd   # positive = LP outperformed
hodl_delta_pct = strategy_pnl_pct - hodl_pnl_pct
```

### Works for All Vaults

The approach does **not** require mint/collect events for the PnL calculation itself. Swap events provide the historical pool price, and V3 math provides the token composition — this works for all vaults including non-rebalanced ones. Mint events are only used for the hybrid start-point clamp (to detect position creation time).

### DB Dependency

The comparison relies on **swap events** being indexed in the pool events database (for historical pool prices). These are populated continuously from pool trading activity and do not depend on the vault having performed any rebalances or fee collections. The `pool.py` event methods now return a `block_time` field used for timestamp-based lookups.

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
- `validator/services/price.py:PriceService.get_token_price()` — current USD prices via CoinGecko
- `validator/services/price.py:PriceService.get_historical_token_price()` — historical USD prices via CoinGecko `market_chart` endpoint
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
- `DbMetrics`: fee0/1 (human + USD), total_fees_usd, collection_count, rebalance_count, start_value_usd, start_timestamp, hodl_value_usd, hodl_pnl_usd, hodl_pnl_pct, strategy_value_usd, strategy_pnl_usd, strategy_pnl_pct, hodl_delta_usd, hodl_delta_pct
- `VaultReport`: vault/pool addresses, token symbols/decimals, current price/tick, unallocated inventory, positions list, total values, uncollected fees, optional DbMetrics
- `ReportParams`: lookback_days, db_enabled, vault_source
- `MinerReport`: timestamp, chain_id, params, list of VaultReports, totals (value, uncollected fees, historical fees, total_strategy_pnl_usd, total_hodl_pnl_usd)

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
11. If `include_db`: compute deployed tokens (positions + uncollected fees, excluding unallocated), pass to `collect_db_metrics()` for historical fees/rebalances and HODL vs Strategy PnL

### 5. DB metrics: `collect_db_metrics(position_manager, ..., deployed0_human, deployed1_human) -> DbMetrics`

1. Receive pre-resolved position manager address and current deployed token totals (positions + uncollected fees, excluding unallocated)
2. Calculate `start_block = current_block - (lookback_days * 43200)`
3. Fetch concurrently: `get_collect_events`, `get_mint_events`, `get_burn_events` (all from `PoolDataDB`)
4. Filter all events by `owner == position_manager_address` (lowercased, no 0x prefix)
5. Aggregate fees: sum `amount0`/`amount1` from collect events
6. Count rebalances: count of mint events
7. HODL vs Strategy PnL:
    - Determine hybrid start point: `max(lookback_start, first_mint_block_time)` using `block_time` from mint events
    - Get historical pool price at start block via `get_sqrt_price_at_block()` (from swap events)
    - Compute starting token composition using V3 math (`get_amounts_for_liquidity` with position ticks + start sqrtPriceX96)
    - Fetch historical USD prices via `PriceService.get_historical_token_price(token, chain_id, start_timestamp)`
    - Compute `start_value_usd = start_token0 * hist_price0 + start_token1 * hist_price1`
    - Compute `hodl_value_usd = start_token0 * current_price0 + start_token1 * current_price1`
    - Compute `strategy_value_usd = deployed0 * current_price0 + deployed1 * current_price1` (includes uncollected fees)
    - Derive PnL and delta values (USD and %)

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

  HODL vs Strategy (30d):
    Start: $105.00 (2026-02-17T12:00:00Z)
    HODL:     $110.00 | PnL: +$5.00 (+4.76%)
    Strategy: $111.02 | PnL: +$6.02 (+5.73%)
    Delta: +$1.02 (+0.97%) — Strategy outperformed

========================================================================================================================
  Portfolio Total: $111.02 | Uncollected Fees: $0.08 | Strategy PnL: +$6.02 | HODL PnL: +$5.00
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
- **HODL vs Strategy uses historical USD prices**: Start value is computed with CoinGecko `market_chart` prices at the start timestamp, not current prices. This gives accurate PnL.
- **Scope is deployed tokens only**: Unallocated inventory is excluded from the HODL vs Strategy comparison. Uncollected fees count toward Strategy value (they are earned by the LP position).
- **Hybrid start point**: Uses lookback start, clamped to position creation (first mint `block_time`) for newer positions.
- **Works for all vaults**: Swap events provide historical pool price, V3 math provides starting token composition. No mint/collect events needed for non-rebalanced vaults.
- **pool.py event methods return `block_time`**: Used for timestamp-based lookups and hybrid start-point clamping.
- **Why DB over on-chain for events**: Fetching historical events from chain would require paginated `eth_getLogs` over ~1.3M blocks (30 days on Base), with most public RPCs limiting to ~10k blocks per request. The subgraph-indexed DB is orders of magnitude faster.

## Verification

1. **Without DB**: Run `python scripts/miner_report.py --no-db` — shows positions, inventory, USD values, and uncollected fees (Tier 1). No fees/HODL vs Strategy sections.
2. **With DB**: Run `python scripts/miner_report.py` — additionally shows historical fees, rebalances, and HODL vs Strategy comparison (Tier 2)
3. **JSON mode**: Run `python scripts/miner_report.py --json` — valid JSON with `uncollected*` fields, `db_metrics` (including `start_value_usd`, `start_timestamp`, `hodl_value_usd`, `hodl_pnl_usd`, `hodl_pnl_pct`, `strategy_value_usd`, `strategy_pnl_usd`, `strategy_pnl_pct`, `hodl_delta_usd`, `hodl_delta_pct`), and top-level `total_strategy_pnl_usd` / `total_hodl_pnl_usd`
4. **Multiple vaults**: All 3 vaults from `MINER_VAULT_ADDRESSES` should report independently
5. **Uncollected fees**: Should be non-zero for vaults with in-range positions
6. **HODL vs Strategy**: Positive `hodl_delta_usd` = Strategy outperformed HODL; negative = HODL would have been better. Uses historical USD prices for start value.
7. **Hybrid start**: For positions newer than the lookback window, the start point should clamp to position creation time (first mint `block_time`)
8. **Non-rebalanced vaults**: HODL vs Strategy should still work — swap events provide price, V3 math provides token amounts

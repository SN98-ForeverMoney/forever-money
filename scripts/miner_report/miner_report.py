#!/usr/bin/env python3
"""
SN98 Miner Vault Performance Report

Reads on-chain state (Tier 1) and optionally enriches with database metrics (Tier 2)
to produce a consolidated view of vault positions, PnL, fees, and rebalance activity.

Usage:
    python scripts/miner_report.py                       # reads config from .env
    python scripts/miner_report.py --vaults 0xABC,0xDEF  # override vault addresses
    python scripts/miner_report.py --json                 # JSON output
    python scripts/miner_report.py --no-db                # skip database queries
    python scripts/miner_report.py --lookback-days 7      # fee lookback (default 30)
"""

import argparse
import asyncio
import json
import logging
import os
import ssl
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import List, Optional, Tuple

# Allow imports from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv()

from tortoise import Tortoise

from validator.repositories.pool import (
    PoolDataDB,
    close_pool_events_db,
)
from validator.services.liqmanager import SnLiqManagerService
from validator.services.price import PriceService
from validator.utils.math import UniswapV3Math
from validator.utils.web3 import AsyncWeb3Helper, ZERO_ADDRESS
from miner.volatility_miner import discover_vault_pool
from web3 import Web3

logger = logging.getLogger(__name__)

BASE_BLOCKS_PER_DAY = 43200  # ~2s block time on Base


# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class PositionReport:
    tick_lower: int
    tick_upper: int
    price_lower: float
    price_upper: float
    width_ticks: int
    allocation0_human: float
    allocation1_human: float
    allocation0_usd: Optional[float]
    allocation1_usd: Optional[float]
    is_in_range: bool


@dataclass
class DbMetrics:
    fee0_human: float = 0.0
    fee1_human: float = 0.0
    fee0_usd: Optional[float] = None
    fee1_usd: Optional[float] = None
    total_fees_usd: Optional[float] = None
    collection_count: int = 0
    rebalance_count: int = 0
    # Token-denominated PnL
    pnl0_human: float = 0.0
    pnl1_human: float = 0.0
    pnl0_usd: Optional[float] = None
    pnl1_usd: Optional[float] = None
    total_pnl_usd: Optional[float] = None
    # HODL comparison (DB-based — requires indexed on-chain events in the pool events DB.
    # If no events exist in the lookback window, e.g. for a newly deployed vault that
    # hasn't had any rebalances or fee collections yet, values will be None.
    # Data populates automatically once on-chain events are indexed into the DB.)
    hodl_value_usd: Optional[float] = None  # Value if tokens were just held
    lp_value_usd: Optional[float] = None  # Actual current vault value
    hodl_delta_usd: Optional[float] = None  # LP - HODL (positive = LP outperformed)


@dataclass
class VaultReport:
    vault_address: str
    pool_address: str
    token0_symbol: str
    token1_symbol: str
    token0_decimals: int
    token1_decimals: int
    current_tick: int
    current_price: float
    tick_spacing: int
    unallocated0_human: float
    unallocated1_human: float
    unallocated0_usd: Optional[float]
    unallocated1_usd: Optional[float]
    positions: List[PositionReport]
    deployed_usd: Optional[float]
    unallocated_usd: Optional[float]
    total_usd: Optional[float]
    uncollected0_human: float = 0.0
    uncollected1_human: float = 0.0
    uncollected0_usd: Optional[float] = None
    uncollected1_usd: Optional[float] = None
    total_uncollected_usd: Optional[float] = None
    db_metrics: Optional[DbMetrics] = None
    error: Optional[str] = None


@dataclass
class ReportParams:
    lookback_days: int
    db_enabled: bool
    vault_source: str  # "env" or "cli"


@dataclass
class MinerReport:
    timestamp: str
    chain_id: int
    params: ReportParams
    vaults: List[VaultReport]
    total_usd: Optional[float] = None
    total_uncollected_usd: Optional[float] = None
    total_fees_usd: Optional[float] = None
    total_pnl_usd: Optional[float] = None
    total_hodl_delta_usd: Optional[float] = None


# ── Helpers ──────────────────────────────────────────────────────────────────


async def get_token_info(chain_id: int, token_address: str) -> Tuple[str, int]:
    """Get (symbol, decimals) for an ERC20 token."""
    w3 = AsyncWeb3Helper.make_web3(chain_id)
    erc20 = w3.make_contract_by_name("ERC20", token_address)
    symbol, decimals = await asyncio.gather(
        erc20.functions.symbol().call(),
        erc20.functions.decimals().call(),
    )
    return symbol, decimals


def tick_to_price(tick: int, decimals0: int, decimals1: int) -> float:
    """Convert a Uniswap V3 tick to a human-readable price (token1/token0)."""
    sqrt_price = UniswapV3Math.get_sqrt_ratio_at_tick(tick)
    return UniswapV3Math.sqrt_price_x96_to_price(sqrt_price, decimals0, decimals1)


def wei_to_human(amount_wei: int, decimals: int) -> float:
    """Convert a wei amount to human-readable using token decimals."""
    return amount_wei / (10**decimals)


def fmt_usd(value: Optional[float]) -> str:
    """Format a USD value or return 'N/A'."""
    if value is None:
        return "N/A"
    return f"${value:,.2f}"


def fmt_amount(value: float, symbol: str, usd: Optional[float] = None) -> str:
    """Format a token amount with optional USD value."""
    usd_str = f" ({fmt_usd(usd)})" if usd is not None else ""
    return (
        f"{value:,.6f} {symbol}{usd_str}"
        if value < 1200
        else f"{value:,.4f} {symbol}{usd_str}"
    )


# ── On-chain fees (Tier 1) ──────────────────────────────────────────────────


async def _try_get_position_manager(
    liq_service: SnLiqManagerService,
    token: str,
) -> Optional[str]:
    """Try to get position manager for a token. Returns None if reverts or zero."""
    try:
        pm = await liq_service.liq_manager.functions.akAddressToPositionManager(
            Web3.to_checksum_address(token)
        ).call()
        return pm if pm != ZERO_ADDRESS else None
    except Exception:
        return None


async def resolve_position_manager(
    liq_service: SnLiqManagerService,
    token0: str,
    token1: str,
) -> Optional[str]:
    """Resolve the position manager address for a vault's AK token."""
    pm0, pm1 = await asyncio.gather(
        _try_get_position_manager(liq_service, token0),
        _try_get_position_manager(liq_service, token1),
    )
    return pm0 or pm1


async def get_uncollected_fees(
    chain_id: int,
    vault_address: str,
    position_manager: str,
    token0: str,
    token1: str,
    dec0: int,
    dec1: int,
    price0_usd: Optional[float],
    price1_usd: Optional[float],
) -> Tuple[float, float, Optional[float], Optional[float], Optional[float]]:
    """Static call to claimFees() to get uncollected fee amounts.

    Must be called with from=vault_address (the LiquidityManager) for access control.
    Returns (amount0_human, amount1_human, amount0_usd, amount1_usd, total_usd).
    """
    w3 = AsyncWeb3Helper.make_web3(chain_id)
    pm_contract = w3.make_contract_by_name("AeroCLPositionManager", position_manager)
    token_amounts = await pm_contract.functions.claimFees().call(
        {"from": vault_address}
    )

    # Parse TokenAmount[] — each is (token_address, amount_wei, fee_receiver)
    token0_check = Web3.to_checksum_address(token0)
    token1_check = Web3.to_checksum_address(token1)
    raw0 = 0
    raw1 = 0
    for ta in token_amounts:
        token_addr = Web3.to_checksum_address(ta[0])
        amount = int(ta[1])
        if token_addr == token0_check:
            raw0 += amount
        elif token_addr == token1_check:
            raw1 += amount

    h0 = wei_to_human(raw0, dec0)
    h1 = wei_to_human(raw1, dec1)
    u0 = h0 * price0_usd if price0_usd is not None else None
    u1 = h1 * price1_usd if price1_usd is not None else None
    total = (u0 + u1) if u0 is not None and u1 is not None else None
    return h0, h1, u0, u1, total


# ── Core report builder ─────────────────────────────────────────────────────


async def build_vault_report(
    vault_address: str,
    chain_id: int,
    include_db: bool,
    lookback_days: int,
) -> VaultReport:
    """Build a full report for a single vault."""
    # 1. Discover pool
    pool_address = await discover_vault_pool(vault_address, chain_id)
    if pool_address is None:
        return VaultReport(
            vault_address=vault_address,
            pool_address="",
            token0_symbol="",
            token1_symbol="",
            token0_decimals=0,
            token1_decimals=0,
            current_tick=0,
            current_price=0.0,
            tick_spacing=0,
            unallocated0_human=0,
            unallocated1_human=0,
            unallocated0_usd=None,
            unallocated1_usd=None,
            positions=[],
            deployed_usd=None,
            unallocated_usd=None,
            total_usd=None,
            error="Pool discovery failed — no registered AK token found",
        )

    # 2. Initialize service
    liq_service = SnLiqManagerService(chain_id, vault_address, pool_address)

    # 3. Gather on-chain state concurrently
    sqrt_price_x96, tick_spacing, positions, inventory, (token0, token1) = (
        await asyncio.gather(
            liq_service.get_current_price(),
            liq_service.get_tick_spacing(),
            liq_service.get_current_positions(),
            liq_service.get_inventory(),
            liq_service._get_pool_tokens(),
        )
    )

    current_tick = UniswapV3Math.get_tick_from_sqrt_price_x96(sqrt_price_x96)

    # 4. Token info + prices (concurrently)
    (sym0, dec0), (sym1, dec1) = await asyncio.gather(
        get_token_info(chain_id, token0),
        get_token_info(chain_id, token1),
    )

    # 5. USD prices — graceful fallback if API fails
    price0_usd: Optional[float] = None
    price1_usd: Optional[float] = None
    try:
        price0_usd, price1_usd = await asyncio.gather(
            PriceService.get_token_price(token0, chain_id),
            PriceService.get_token_price(token1, chain_id),
        )
    except Exception as e:
        logger.warning(f"Price fetch failed for vault {vault_address}: {e}")

    current_price = UniswapV3Math.sqrt_price_x96_to_price(sqrt_price_x96, dec0, dec1)

    # 6. Build position reports
    position_reports: List[PositionReport] = []
    total_deployed0_usd = 0.0
    total_deployed1_usd = 0.0
    for pos in positions:
        a0 = wei_to_human(int(pos.allocation0), dec0)
        a1 = wei_to_human(int(pos.allocation1), dec1)
        a0_usd = a0 * price0_usd if price0_usd is not None else None
        a1_usd = a1 * price1_usd if price1_usd is not None else None
        if a0_usd is not None:
            total_deployed0_usd += a0_usd
        if a1_usd is not None:
            total_deployed1_usd += a1_usd

        pl = tick_to_price(pos.tick_lower, dec0, dec1)
        pu = tick_to_price(pos.tick_upper, dec0, dec1)

        position_reports.append(
            PositionReport(
                tick_lower=pos.tick_lower,
                tick_upper=pos.tick_upper,
                price_lower=pl,
                price_upper=pu,
                width_ticks=pos.tick_upper - pos.tick_lower,
                allocation0_human=a0,
                allocation1_human=a1,
                allocation0_usd=a0_usd,
                allocation1_usd=a1_usd,
                is_in_range=pos.tick_lower <= current_tick <= pos.tick_upper,
            )
        )

    # 7. Unallocated inventory
    s0 = wei_to_human(int(inventory.amount0), dec0)
    s1 = wei_to_human(int(inventory.amount1), dec1)
    s0_usd = s0 * price0_usd if price0_usd is not None else None
    s1_usd = s1 * price1_usd if price1_usd is not None else None

    # 8. Totals
    deployed_usd = (
        (total_deployed0_usd + total_deployed1_usd) if price0_usd is not None else None
    )
    unallocated_usd = (
        (s0_usd + s1_usd) if s0_usd is not None and s1_usd is not None else None
    )
    total_usd = (
        (deployed_usd + unallocated_usd)
        if deployed_usd is not None and unallocated_usd is not None
        else None
    )

    # 9. Resolve position manager (needed for Tier 1 fees and Tier 2 DB metrics)
    position_manager = await resolve_position_manager(liq_service, token0, token1)

    # 10. On-chain uncollected fees (Tier 1)
    uc0, uc1, uc0_usd, uc1_usd, total_uc_usd = 0.0, 0.0, None, None, None
    if position_manager:
        try:
            uc0, uc1, uc0_usd, uc1_usd, total_uc_usd = await get_uncollected_fees(
                chain_id,
                vault_address,
                position_manager,
                token0,
                token1,
                dec0,
                dec1,
                price0_usd,
                price1_usd,
            )
        except Exception as e:
            logger.warning(f"Failed to fetch uncollected fees for {vault_address}: {e}")

    # 11. DB metrics (Tier 2)
    db_metrics = None
    if include_db and position_manager:
        # Total tokens in vault (positions + unallocated + uncollected fees)
        current_total0_human = (
            sum(p.allocation0_human for p in position_reports) + s0 + uc0
        )
        current_total1_human = (
            sum(p.allocation1_human for p in position_reports) + s1 + uc1
        )
        db_metrics = await collect_db_metrics(
            position_manager,
            pool_address,
            lookback_days,
            dec0,
            dec1,
            price0_usd,
            price1_usd,
            chain_id,
            current_total0_human=current_total0_human,
            current_total1_human=current_total1_human,
        )

    return VaultReport(
        vault_address=vault_address,
        pool_address=pool_address,
        token0_symbol=sym0,
        token1_symbol=sym1,
        token0_decimals=dec0,
        token1_decimals=dec1,
        current_tick=current_tick,
        current_price=current_price,
        tick_spacing=tick_spacing,
        unallocated0_human=s0,
        unallocated1_human=s1,
        unallocated0_usd=s0_usd,
        unallocated1_usd=s1_usd,
        positions=position_reports,
        deployed_usd=deployed_usd,
        unallocated_usd=unallocated_usd,
        total_usd=total_usd,
        uncollected0_human=uc0,
        uncollected1_human=uc1,
        uncollected0_usd=uc0_usd,
        uncollected1_usd=uc1_usd,
        total_uncollected_usd=total_uc_usd,
        db_metrics=db_metrics,
    )


# ── DB metrics (Tier 2) ─────────────────────────────────────────────────────


async def collect_db_metrics(
    position_manager: str,
    pool_address: str,
    lookback_days: int,
    dec0: int,
    dec1: int,
    price0_usd: Optional[float],
    price1_usd: Optional[float],
    chain_id: int,
    current_total0_human: float = 0.0,
    current_total1_human: float = 0.0,
) -> Optional[DbMetrics]:
    """Query fee, rebalance, PnL, and HODL comparison data from pool events DB.

    The HODL comparison reconstructs the vault's starting token balances from
    mint/collect events in the DB. If no events exist in the lookback window
    (e.g., a newly deployed vault), HODL values will be None. Data populates
    automatically once rebalances/fee collections are indexed into the DB.
    """
    try:
        pm_clean = position_manager.lower().replace("0x", "")

        # Calculate lookback start block
        w3 = AsyncWeb3Helper.make_web3(chain_id)
        current_block = await w3.web3.eth.get_block_number()
        start_block = current_block - (lookback_days * BASE_BLOCKS_PER_DAY)

        db = PoolDataDB()

        # Fetch events concurrently
        collect_events, mint_events, burn_events = await asyncio.gather(
            db.get_collect_events(pool_address, start_block=start_block),
            db.get_mint_events(pool_address, start_block=start_block),
            db.get_burn_events(pool_address, start_block=start_block),
        )

        # Filter by owner == position manager
        my_collects = [e for e in collect_events if e["owner"] == pm_clean]
        my_mints = [e for e in mint_events if e["owner"] == pm_clean]
        my_burns = [e for e in burn_events if e["owner"] == pm_clean]

        # Aggregate fees
        total_fee0 = sum(int(e["amount0"]) for e in my_collects)
        total_fee1 = sum(int(e["amount1"]) for e in my_collects)
        fee0_human = wei_to_human(total_fee0, dec0)
        fee1_human = wei_to_human(total_fee1, dec1)
        fee0_usd = fee0_human * price0_usd if price0_usd is not None else None
        fee1_usd = fee1_human * price1_usd if price1_usd is not None else None
        total_fees_usd = (
            (fee0_usd + fee1_usd)
            if fee0_usd is not None and fee1_usd is not None
            else None
        )

        # PnL: tokens_out (burns + fees) - tokens_in (mints)
        total_in0 = sum(int(e["amount0"]) for e in my_mints)
        total_in1 = sum(int(e["amount1"]) for e in my_mints)
        total_out0 = sum(int(e["amount0"]) for e in my_burns) + total_fee0
        total_out1 = sum(int(e["amount1"]) for e in my_burns) + total_fee1
        pnl0 = total_out0 - total_in0
        pnl1 = total_out1 - total_in1
        pnl0_human = wei_to_human(pnl0, dec0)
        pnl1_human = wei_to_human(pnl1, dec1)
        pnl0_usd = pnl0_human * price0_usd if price0_usd is not None else None
        pnl1_usd = pnl1_human * price1_usd if price1_usd is not None else None
        total_pnl_usd = (
            (pnl0_usd + pnl1_usd)
            if pnl0_usd is not None and pnl1_usd is not None
            else None
        )

        # HODL comparison: what if we just held the starting tokens?
        # Net token flow from LP activity = collects - mints (actual token transfers).
        # Mints transfer tokens OUT of vault into pool; collects transfer tokens IN.
        # Burns don't transfer tokens (just update owed amounts in the pool).
        hodl_value_usd = lp_value_usd = hodl_delta_usd = None
        has_events = len(my_mints) > 0 or len(my_collects) > 0
        if has_events and price0_usd is not None and price1_usd is not None:
            net_flow0_human = wei_to_human(total_fee0 - total_in0, dec0)
            net_flow1_human = wei_to_human(total_fee1 - total_in1, dec1)

            start0 = current_total0_human - net_flow0_human
            start1 = current_total1_human - net_flow1_human

            hodl_value_usd = start0 * price0_usd + start1 * price1_usd
            lp_value_usd = (
                current_total0_human * price0_usd + current_total1_human * price1_usd
            )
            hodl_delta_usd = lp_value_usd - hodl_value_usd

        return DbMetrics(
            fee0_human=fee0_human,
            fee1_human=fee1_human,
            fee0_usd=fee0_usd,
            fee1_usd=fee1_usd,
            total_fees_usd=total_fees_usd,
            collection_count=len(my_collects),
            rebalance_count=len(my_mints),
            pnl0_human=pnl0_human,
            pnl1_human=pnl1_human,
            pnl0_usd=pnl0_usd,
            pnl1_usd=pnl1_usd,
            total_pnl_usd=total_pnl_usd,
            hodl_value_usd=hodl_value_usd,
            lp_value_usd=lp_value_usd,
            hodl_delta_usd=hodl_delta_usd,
        )
    except Exception as e:
        logger.warning(f"DB metrics collection failed: {e}")
        return None


# ── Output formatting ────────────────────────────────────────────────────────


def print_report(report: MinerReport) -> None:
    """Print a human-readable report to stdout."""
    p = report.params
    db_str = "on" if p.db_enabled else "off"
    print(f"\n{'=' * 120}")
    print(f"  SN98 Miner Vault Report")
    print(f"  Generated: {report.timestamp} | Chain: Base ({report.chain_id})")
    print(
        f"  Params: lookback={p.lookback_days}d | db={db_str} | vaults={len(report.vaults)}"
    )
    print(f"{'=' * 120}")

    for v in report.vaults:
        print(f"\n{'─' * 120}")
        print(f"  Vault: {v.vault_address}")

        if v.error:
            print(f"  ERROR: {v.error}")
            continue

        print(f"  Pool:  {v.pool_address}")
        print(f"  Pair:  {v.token0_symbol} / {v.token1_symbol}")
        print(
            f"  Price: {v.current_price:,.6f} {v.token1_symbol}/{v.token0_symbol} "
            f"(tick {v.current_tick}) | Tick Spacing: {v.tick_spacing}"
        )

        # Unallocated inventory
        print(f"\n  Unallocated Inventory:")
        print(
            f"    {fmt_amount(v.unallocated0_human, v.token0_symbol, v.unallocated0_usd)}"
        )
        print(
            f"    {fmt_amount(v.unallocated1_human, v.token1_symbol, v.unallocated1_usd)}"
        )

        # Positions
        in_range_count = sum(1 for p in v.positions if p.is_in_range)
        print(f"\n  Positions ({len(v.positions)} total, {in_range_count} in range):")
        if not v.positions:
            print(f"    (none)")
        for i, p in enumerate(v.positions, 1):
            status = "IN RANGE" if p.is_in_range else "OUT OF RANGE"
            print(
                f"    [{i}] ticks [{p.tick_lower}, {p.tick_upper}] "
                f"→ {p.price_lower:,.6f} - {p.price_upper:,.6f} {v.token1_symbol}/{v.token0_symbol}"
            )
            print(f"        Width: {p.width_ticks} ticks | Status: {status}")
            print(
                f"        {fmt_amount(p.allocation0_human, v.token0_symbol, p.allocation0_usd)}"
            )
            print(
                f"        {fmt_amount(p.allocation1_human, v.token1_symbol, p.allocation1_usd)}"
            )

        # Value summary
        print(f"\n  Value:")
        print(
            f"    Deployed: {fmt_usd(v.deployed_usd)} | "
            f"Unallocated: {fmt_usd(v.unallocated_usd)} | "
            f"Total: {fmt_usd(v.total_usd)}"
        )

        # Uncollected fees (Tier 1, on-chain)
        if v.uncollected0_human > 0 or v.uncollected1_human > 0:
            print(f"\n  Uncollected Fees:")
            print(
                f"    {fmt_amount(v.uncollected0_human, v.token0_symbol, v.uncollected0_usd)} | "
                f"{fmt_amount(v.uncollected1_human, v.token1_symbol, v.uncollected1_usd)}"
            )
            print(f"    Total: {fmt_usd(v.total_uncollected_usd)}")

        # DB metrics (Tier 2)
        if v.db_metrics is not None:
            db = v.db_metrics
            print(f"\n  Fees ({report.timestamp[:10]} lookback):")
            print(f"    {fmt_amount(db.fee0_human, v.token0_symbol, db.fee0_usd)}")
            print(f"    {fmt_amount(db.fee1_human, v.token1_symbol, db.fee1_usd)}")
            print(f"    Total Fees: {fmt_usd(db.total_fees_usd)}")
            print(
                f"    Collections: {db.collection_count} | Rebalances: {db.rebalance_count}"
            )

            # PnL section
            def fmt_pnl(value: float, symbol: str, usd: Optional[float]) -> str:
                sign = "+" if value >= 0 else ""
                usd_str = ""
                if usd is not None:
                    usd_sign = "+" if usd >= 0 else ""
                    usd_str = (
                        f" ({usd_sign}${abs(usd):,.2f})"
                        if usd >= 0
                        else f" (-${abs(usd):,.2f})"
                    )
                if abs(value) < 1200:
                    return f"{symbol}: {sign}{value:,.6f}{usd_str}"
                return f"{symbol}: {sign}{value:,.4f}{usd_str}"

            pnl_parts = [
                fmt_pnl(db.pnl0_human, v.token0_symbol, db.pnl0_usd),
                fmt_pnl(db.pnl1_human, v.token1_symbol, db.pnl1_usd),
            ]
            print(f"\n  PnL ({report.params.lookback_days}d, approximate):")
            print(f"    {' | '.join(pnl_parts)}")
            if db.total_pnl_usd is not None:
                pnl_sign = "+" if db.total_pnl_usd >= 0 else "-"
                print(f"    Net PnL: {pnl_sign}${abs(db.total_pnl_usd):,.2f}")

            # HODL comparison (DB-based — requires indexed on-chain events)
            if db.hodl_delta_usd is not None:
                print(
                    f"\n  HODL Comparison "
                    f"({report.params.lookback_days}d, DB-based — requires indexed on-chain events):"
                )
                print(
                    f"    HODL Value: {fmt_usd(db.hodl_value_usd)} | "
                    f"LP Value: {fmt_usd(db.lp_value_usd)}"
                )
                delta_sign = "+" if db.hodl_delta_usd >= 0 else "-"
                label = (
                    "LP outperformed" if db.hodl_delta_usd >= 0 else "HODL outperformed"
                )
                print(
                    f"    LP vs HODL: {delta_sign}${abs(db.hodl_delta_usd):,.2f} ({label})"
                )

    # Portfolio totals
    print(f"\n{'=' * 120}")
    totals = []
    totals.append(f"Portfolio Total: {fmt_usd(report.total_usd)}")
    if report.total_uncollected_usd is not None:
        totals.append(f"Uncollected Fees: {fmt_usd(report.total_uncollected_usd)}")
    if report.total_fees_usd is not None:
        totals.append(f"Total Fees: {fmt_usd(report.total_fees_usd)}")
    if report.total_pnl_usd is not None:
        pnl_sign = "+" if report.total_pnl_usd >= 0 else "-"
        totals.append(f"Net PnL: {pnl_sign}${abs(report.total_pnl_usd):,.2f}")
    if report.total_hodl_delta_usd is not None:
        delta_sign = "+" if report.total_hodl_delta_usd >= 0 else "-"
        totals.append(
            f"LP vs HODL: {delta_sign}${abs(report.total_hodl_delta_usd):,.2f}"
        )
    print(f"  {' | '.join(totals)}")
    print(f"{'=' * 120}\n")


def report_to_json(report: MinerReport) -> str:
    """Serialize the report to JSON."""
    return json.dumps(asdict(report), indent=2, default=str)


# ── Main ─────────────────────────────────────────────────────────────────────


async def async_main(args: argparse.Namespace) -> None:
    chain_id = args.chain_id

    # Resolve vault addresses
    if args.vaults:
        vault_addresses = [v.strip() for v in args.vaults.split(",")]
        vault_source = "cli"
    else:
        raw = os.environ.get("MINER_VAULT_ADDRESSES", "")
        if not raw:
            print(
                "Error: No vault addresses provided. Use --vaults or set MINER_VAULT_ADDRESSES in .env",
                file=sys.stderr,
            )
            sys.exit(1)
        vault_addresses = json.loads(raw)
        vault_source = "env"

    # Checksum addresses
    vault_addresses = [Web3.to_checksum_address(v) for v in vault_addresses]

    # Initialize DB if needed
    include_db = not args.no_db
    if include_db:
        db_host = os.environ.get("JOBS_POSTGRES_HOST", "")
        db_port = os.environ.get("JOBS_POSTGRES_PORT", "5432")
        db_name = os.environ.get("JOBS_POSTGRES_DB", "postgres")
        db_user = os.environ.get("JOBS_POSTGRES_USER", "")
        db_pass = os.environ.get("JOBS_POSTGRES_PASSWORD", "")
        if db_host and db_name and db_user:
            try:
                ssl_ctx = ssl.create_default_context()
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE
                await Tortoise.init(
                    config={
                        "connections": {
                            "default": {
                                "engine": "tortoise.backends.asyncpg",
                                "credentials": {
                                    "host": db_host,
                                    "port": int(db_port),
                                    "user": db_user,
                                    "password": db_pass,
                                    "database": db_name,
                                    "ssl": ssl_ctx,
                                },
                            }
                        },
                        "apps": {
                            "pool_events": {
                                "models": ["validator.models.pool_events"],
                                "default_connection": "default",
                            }
                        },
                    }
                )
                logger.info("Pool events DB initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize pool events DB: {e}")
                include_db = False
        else:
            if args.verbose:
                logger.info(
                    "JOBS_POSTGRES_HOST/DB/USER not set — skipping Tier 2 metrics"
                )
            include_db = False

    # Build reports for each vault
    vault_reports: List[VaultReport] = []
    for vault_addr in vault_addresses:
        try:
            report = await build_vault_report(
                vault_addr, chain_id, include_db, args.lookback_days
            )
            vault_reports.append(report)
        except Exception as e:
            logger.error(f"Failed to build report for {vault_addr}: {e}")
            vault_reports.append(
                VaultReport(
                    vault_address=vault_addr,
                    pool_address="",
                    token0_symbol="",
                    token1_symbol="",
                    token0_decimals=0,
                    token1_decimals=0,
                    current_tick=0,
                    current_price=0.0,
                    tick_spacing=0,
                    unallocated0_human=0,
                    unallocated1_human=0,
                    unallocated0_usd=None,
                    unallocated1_usd=None,
                    positions=[],
                    deployed_usd=None,
                    unallocated_usd=None,
                    total_usd=None,
                    error=str(e),
                )
            )

    # Compute portfolio totals
    vault_totals = [v.total_usd for v in vault_reports if v.total_usd is not None]
    total_usd = sum(vault_totals) if vault_totals else None
    uc_totals = [
        v.total_uncollected_usd
        for v in vault_reports
        if v.total_uncollected_usd is not None
    ]
    total_uncollected_usd = sum(uc_totals) if uc_totals else None
    fee_totals = [
        v.db_metrics.total_fees_usd
        for v in vault_reports
        if v.db_metrics is not None and v.db_metrics.total_fees_usd is not None
    ]
    total_fees_usd = sum(fee_totals) if fee_totals else None
    pnl_totals = [
        v.db_metrics.total_pnl_usd
        for v in vault_reports
        if v.db_metrics is not None and v.db_metrics.total_pnl_usd is not None
    ]
    total_pnl_usd = sum(pnl_totals) if pnl_totals else None
    hodl_delta_totals = [
        v.db_metrics.hodl_delta_usd
        for v in vault_reports
        if v.db_metrics is not None and v.db_metrics.hodl_delta_usd is not None
    ]
    total_hodl_delta_usd = sum(hodl_delta_totals) if hodl_delta_totals else None

    miner_report = MinerReport(
        timestamp=datetime.now(timezone.utc).isoformat(),
        chain_id=chain_id,
        params=ReportParams(
            lookback_days=args.lookback_days,
            db_enabled=include_db,
            vault_source=vault_source,
        ),
        vaults=vault_reports,
        total_usd=total_usd,
        total_uncollected_usd=total_uncollected_usd,
        total_fees_usd=total_fees_usd,
        total_pnl_usd=total_pnl_usd,
        total_hodl_delta_usd=total_hodl_delta_usd,
    )

    # Output
    if args.json:
        print(report_to_json(miner_report))
    else:
        print_report(miner_report)

    # Cleanup DB
    if include_db:
        try:
            await close_pool_events_db()
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description="SN98 Miner Vault Performance Report")
    parser.add_argument(
        "--vaults",
        type=str,
        default=None,
        help="Comma-separated vault addresses (overrides MINER_VAULT_ADDRESSES)",
    )
    parser.add_argument(
        "--chain-id",
        type=int,
        default=int(os.environ.get("MINER_VAULT_CHAIN_ID", "8453")),
        help="Chain ID (default: 8453 for Base)",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--no-db", action="store_true", help="Skip database queries (Tier 2)"
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=30,
        help="Fee lookback period in days (default: 30)",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")

    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")

    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()

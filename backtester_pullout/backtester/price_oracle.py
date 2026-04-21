"""Derive USD price of any pool's tokens from sqrt_price_x96 at a block.

Works when one of the pool's tokens is a known "anchor" with a USD price
(USDC/USDT/DAI = $1, WETH, cbBTC). The other token's USD price is then
derived from the pool's current price ratio.

Used instead of CoinGecko/GeckoTerminal to avoid rate limits + get the
price AT THE EXACT BACKTEST START BLOCK.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from validator.utils.math import UniswapV3Math


# USD price of known tokens. For stable anchors it's ~exactly 1; for vols,
# these are approximate current values used only as the "reference" when
# computing the OTHER token's price. The relative math still works.
KNOWN_TOKEN_USD_PRICE = {
    "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913": ("USDC",  1.0,     6),
    "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb": ("DAI",   1.0,     18),
    "0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA": ("USDbC", 1.0,     6),
    "0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2": ("USDT",  1.0,     6),
    "0x4200000000000000000000000000000000000006": ("WETH",  3000.0,  18),
    "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf": ("cbBTC", 100000.0, 8),
}


def is_known_anchor(addr_cs: str) -> bool:
    return addr_cs in KNOWN_TOKEN_USD_PRICE


def anchor_usd(addr_cs: str) -> Optional[float]:
    info = KNOWN_TOKEN_USD_PRICE.get(addr_cs)
    return info[1] if info else None


@dataclass
class PoolPrices:
    """USD prices of token0 and token1 at a given sqrt_price_x96."""
    token0_usd: float
    token1_usd: float


def derive_pool_prices(
    sqrt_price_x96: int,
    decimals0: int,
    decimals1: int,
    token0_addr_cs: str,
    token1_addr_cs: str,
) -> PoolPrices:
    """Compute USD price of each token given pool state.

    Pool price_raw = token1_raw / token0_raw = (sqrt_price_x96 / 2^96)^2
    Human price of token0 in token1 = price_raw × 10^(dec1 - dec0)  (inverted)
    Human price of token1 in token0 = 1/price_raw × 10^(dec0 - dec1)

    Exactly one of the two tokens must be a known anchor; we derive the other.
    """
    if is_known_anchor(token0_addr_cs) and is_known_anchor(token1_addr_cs):
        return PoolPrices(
            token0_usd=anchor_usd(token0_addr_cs),
            token1_usd=anchor_usd(token1_addr_cs),
        )

    Q96 = UniswapV3Math.Q96
    # token1 per 1 token0, in human units
    price_t1_per_t0 = (sqrt_price_x96 / Q96) ** 2 * (10 ** (decimals0 - decimals1))

    if is_known_anchor(token0_addr_cs):
        # token0 is the anchor → token1_usd = token0_usd / price_t1_per_t0
        t0_usd = anchor_usd(token0_addr_cs)
        if price_t1_per_t0 <= 0:
            raise ValueError("pool price is zero; cannot derive")
        t1_usd = t0_usd / price_t1_per_t0
        return PoolPrices(token0_usd=t0_usd, token1_usd=t1_usd)

    if is_known_anchor(token1_addr_cs):
        # token1 is the anchor → token0_usd = token1_usd × price_t1_per_t0
        t1_usd = anchor_usd(token1_addr_cs)
        t0_usd = t1_usd * price_t1_per_t0
        return PoolPrices(token0_usd=t0_usd, token1_usd=t1_usd)

    raise ValueError(
        f"neither token0 ({token0_addr_cs}) nor token1 ({token1_addr_cs}) "
        "is a known anchor; can't derive USD prices without an external oracle"
    )


def position_size_token1_raw_for_usd(
    position_usd: float, token1_usd_price: float, decimals1: int,
) -> int:
    """Compute raw token1 amount equivalent to a USD value."""
    if token1_usd_price <= 0:
        raise ValueError("token1_usd_price must be positive")
    return int(position_usd / token1_usd_price * 10 ** decimals1)

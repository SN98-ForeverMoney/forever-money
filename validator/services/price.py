import aiohttp
import asyncio
import logging
from typing import Dict

import bittensor as bt

from validator.utils.cache import async_ttl_cache

logger = logging.getLogger(__name__)


class PriceService:
    """Service to fetch token prices, including TWAP Alpha Price."""

    BASE_URL = "https://api.coingecko.com/api/v3"
    COINGECKO_TAO_ID = "bittensor"  # Bittensor TAO on Coingecko
    COINGECKO_ALPHA_ID = "forevermoney"  # Alpha on Coingecko

    # CoinGecko asset platform IDs by chain_id (for /coins/{id}/contract/{addr}/market_chart)
    CHAIN_ID_TO_PLATFORM: Dict[int, str] = {
        1: "ethereum",
        8453: "base",
        137: "polygon-pos",
        42161: "arbitrum-one",
        10: "optimistic-ethereum",
        43114: "avalanche",
        56: "binance-smart-chain",
    }

    # GeckoTerminal network names by chain_id (fallback when CoinGecko returns 404)
    GECKOTERMINAL_BASE_URL = "https://api.geckoterminal.com/api/v2"
    CHAIN_ID_TO_NETWORK: Dict[int, str] = {
        1: "eth",
        8453: "base",
        137: "polygon_pos",
        42161: "arbitrum",
        10: "optimism",
        43114: "avax",
        56: "bsc",
    }

    @staticmethod
    @async_ttl_cache(ttl=2.0)
    async def get_tao_price_usd() -> float:
        """
        Get current price of TAO (Bittensor) token in USD from Coingecko.

        Returns:
            TAO price in USD, or 1.0 as fallback
        """
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{PriceService.BASE_URL}/simple/price"
                params = {
                    "ids": PriceService.COINGECKO_TAO_ID,
                    "vs_currencies": "usd",
                }
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        data = await response.json()
                        tao_price = data.get(PriceService.COINGECKO_TAO_ID, {}).get("usd", 1.0)
                        return float(tao_price)
                    else:
                        logger.warning(f"Coingecko API returned status {response.status}")
        except asyncio.TimeoutError:
            logger.warning("Timeout fetching TAO price from Coingecko")
        except Exception as e:
            logger.error(f"Failed to fetch TAO price: {e}")

    @staticmethod
    @async_ttl_cache(ttl=2.0)
    async def get_alpha_price_tao(subtensor: bt.Subtensor, netuid: int) -> float:
        """
        Get Alpha price in TAO (how many TAO per 1 Alpha).

        Uses subtensor subnet info: alpha_to_tao(1) = TAO/Alpha spot ratio.

        Returns:
            Alpha price in TAO (TAO per 1 Alpha)
        """
        try:
            subnet_info = subtensor.subnet(netuid)
            alpha_price_tao = subnet_info.alpha_to_tao(1)
            return float(alpha_price_tao)
        except Exception as e:
            logger.error(f"Failed to fetch Alpha price (TAO): {e}")

    @staticmethod
    @async_ttl_cache(ttl=2.0)
    async def get_alpha_price_usd(subtensor: bt.Subtensor, netuid: int) -> float:
        """
        Get Alpha price in USD using tao_price_usd and alpha_price_tao.

        Alpha (USD) = alpha_price_tao * tao_price_usd
        i.e. (TAO per Alpha) * (USD per TAO) = USD per Alpha.

        Returns:
            Alpha price in USD
        """
        try:
            tao_price_usd = await PriceService.get_tao_price_usd()
            alpha_price_tao = await PriceService.get_alpha_price_tao(subtensor, netuid)
            alpha_price_usd = alpha_price_tao * tao_price_usd      
            return alpha_price_usd
        except Exception as e:
            logger.error(f"Failed to fetch Alpha price (USD): {e}")

    @staticmethod
    @async_ttl_cache(ttl=2.0)
    async def get_token_price(token_address: str, chain_id: int = 8453) -> float:
        """
        Get current token price in USD from CoinGecko.

        Raises:
            ValueError: On invalid args (unknown chain_id, empty token_address).
            RuntimeError: On fetch failure (timeout, HTTP error, no prices).
            Exception: Re-raised from underlying errors.

        Returns:
            Token price in USD.
        """
        platform = PriceService.CHAIN_ID_TO_PLATFORM.get(chain_id)
        if not platform:
            raise ValueError(
                f"chain_id={chain_id} not in CHAIN_ID_TO_PLATFORM"
            )

        raw = (token_address or "").strip()
        if not raw:
            raise ValueError("empty token_address")
        low = raw.lower()
        addr = low if low.startswith("0x") else "0x" + low

        url = (
            f"{PriceService.BASE_URL}/coins/{platform}/contract/{addr}/market_chart"
        )
        params = {"vs_currency": "usd", "days": "1"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, params=params, timeout=aiohttp.ClientTimeout(total=15)
                ) as response:
                    if response.status == 404:
                        logger.info(
                            f"CoinGecko 404 for {token_address} on {platform}, "
                            f"falling back to GeckoTerminal"
                        )
                        return await PriceService._get_token_price_geckoterminal(
                            addr, chain_id
                        )
                    if response.status != 200:
                        raise RuntimeError(
                            f"CoinGecko returned status {response.status} "
                            f"for {token_address} on {platform}"
                        )
                    data = await response.json()
        except asyncio.TimeoutError as e:
            logger.warning(
                f"Timeout fetching token price for {token_address} (chain_id={chain_id})"
            )
            raise RuntimeError(
                f"Timeout fetching token price for {token_address} "
                f"(chain_id={chain_id})"
            ) from e
        except (ValueError, RuntimeError):
            raise
        except Exception as e:
            logger.error(f"Failed to fetch token price for {token_address}: {e}")
            raise

        prices = data.get("prices") or []
        if not prices:
            raise RuntimeError(
                f"No prices returned for {token_address} on {platform}"
            )

        # prices = [[timestamp_ms, price], ...]; use latest (last) price
        prices.sort(key=lambda p: p[0])
        _, last_price = prices[-1]
        return float(last_price)

    @staticmethod
    async def _get_token_price_geckoterminal(token_address: str, chain_id: int) -> float:
        """
        Fallback: get token price in USD from GeckoTerminal.

        Raises:
            ValueError: If chain_id has no GeckoTerminal network mapping.
            RuntimeError: On fetch failure or missing price data.
        """
        network = PriceService.CHAIN_ID_TO_NETWORK.get(chain_id)
        if not network:
            raise ValueError(
                f"chain_id={chain_id} not in CHAIN_ID_TO_NETWORK"
            )

        url = (
            f"{PriceService.GECKOTERMINAL_BASE_URL}/simple/networks/{network}"
            f"/token_price/{token_address}"
        )

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=15)
                ) as response:
                    if response.status != 200:
                        raise RuntimeError(
                            f"GeckoTerminal returned status {response.status} "
                            f"for {token_address} on {network}"
                        )
                    data = await response.json()
        except asyncio.TimeoutError as e:
            raise RuntimeError(
                f"Timeout fetching token price from GeckoTerminal "
                f"for {token_address} (chain_id={chain_id})"
            ) from e
        except RuntimeError:
            raise
        except Exception as e:
            logger.error(
                f"Failed to fetch token price from GeckoTerminal "
                f"for {token_address}: {e}"
            )
            raise

        token_prices = (
            data.get("data", {})
            .get("attributes", {})
            .get("token_prices", {})
        )
        price_str = token_prices.get(token_address)
        if not price_str:
            raise RuntimeError(
                f"No price returned from GeckoTerminal for {token_address} on {network}"
            )

        return float(price_str)

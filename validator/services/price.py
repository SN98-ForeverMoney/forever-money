import aiohttp
import logging
from typing import Optional
import bittensor as bt
import asyncio

logger = logging.getLogger(__name__)


class PriceService:
    """Service to fetch token prices, including TWAP Alpha Price."""

    BASE_URL = "https://api.coingecko.com/api/v3"
    COINGECKO_TAO_ID = "bittensor"  # Bittensor TAO on Coingecko
    COINGECKO_ALPHA_ID = "forevermoney"  # Alpha on Coingecko

    @staticmethod
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
                        return 1.0
        except asyncio.TimeoutError:
            logger.warning("Timeout fetching TAO price from Coingecko")
            return 1.0
        except Exception as e:
            logger.error(f"Failed to fetch TAO price: {e}")
            return 1.0

    @staticmethod
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
            return 1.0

    @staticmethod
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
            return 1.0

    @staticmethod
    async def get_token_price(token_address: str, chain_id: int = 8453) -> float:
        """
        Get token price from Coingecko or on-chain DEX.

        Args:
            token_address: Token contract address
            chain_id: Chain ID (e.g., 8453 for Base)

        Returns:
            Token price in USD
        """
        # TODO: Implement real fetching from Coingecko or DEX
        # For now, return placeholder
        logger.warning(f"get_token_price not fully implemented for {token_address}")
        return 1.0
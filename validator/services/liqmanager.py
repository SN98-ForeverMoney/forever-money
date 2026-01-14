"""
Inventory provider implementations for SN98 ForeverMoney.

This module provides different methods for obtaining token inventory
for LP strategy generation.
"""
import asyncio
import logging
from typing import Optional, Tuple, List

from web3 import Web3
from web3.contract import AsyncContract

from protocol import Inventory, Position
from validator.utils.math import UniswapV3Math
from validator.utils.web3 import AsyncWeb3Helper, ZERO_ADDRESS

logger = logging.getLogger(__name__)


class SnLiqManagerService:
    """SnLiqManager"""

    def __init__(
        self,
        chain_id: int,
        liquidity_manager_address: str,
        pool_address: str,
    ):
        """Initialize the LiquidityManager inventory provider."""
        self.chain_id = chain_id
        self.liq_manager: AsyncContract = AsyncWeb3Helper.make_web3(chain_id).make_contract_by_name(
            name="LiquidityManager",
            addr=liquidity_manager_address,
        )
        self.pool: AsyncContract = AsyncWeb3Helper.make_web3(chain_id).make_contract_by_name(
            name="ICLPool",
            addr=pool_address,
        )

    async def _get_pool_tokens(self) -> Tuple[str, str]:
        """
        Extract token0 and token1 addresses from a pool.
        Returns:
            Tuple of (token0_address, token1_address)

        Raises:
            ValueError: If tokens cannot be extracted
        """
        try:
            token0, token1 = await asyncio.gather(
                self.pool.functions.token0().call(),
                self.pool.functions.token1().call(),
            )
            logger.info(
                f"Extracted tokens from pool {self.pool.address}: token0={token0}, token1={token1}"
            )
            return token0, token1

        except Exception as e:
            raise ValueError(
                f"Failed to extract tokens from pool {self.pool.address}: {e}"
            )

    async def _find_registered_ak(self, token_address: str) -> Optional[str]:
        """
        Check if a token is registered as an AK using akAddressToPoolManager.

        Args:
            token_address: The token address to check

        Returns:
            The token address if registered, None if reverts
        """
        try:
            # Call akAddressToPoolManager
            pool_manager = await self.liq_manager.functions.akAddressToPoolManager(
                Web3.to_checksum_address(token_address)
            ).call()

            # If it returns a non-zero address, the token is registered
            if pool_manager != ZERO_ADDRESS:
                logger.info(
                    f"Token {token_address} is registered with PoolManager {pool_manager}"
                )
                return token_address
            else:
                logger.info(
                    f"Token {token_address} returned zero address - not registered"
                )
                return None

        except Exception as e:
            logger.info(f"Token {token_address} not registered (call reverted): {e}")
            return None

    async def _get_stashed_tokens(self, ak_address: str, token_address: str) -> int:
        """
        Get stashed token amount using akToStashedTokens.

        Args:
            ak_address: The registered AK address
            token_address: The token address to query

        Returns:
            Amount of stashed tokens (in wei)
        """
        try:
            amount = await self.liq_manager.functions.akToStashedTokens(
                Web3.to_checksum_address(ak_address),
                Web3.to_checksum_address(token_address),
            ).call()

            logger.info(
                f"Stashed tokens for AK {ak_address}, token {token_address}: {amount}"
            )
            return amount

        except Exception as e:
            logger.warning(
                f"Failed to query stashed tokens for {ak_address}/{token_address}: {e}"
            )
            return 0

    async def get_inventory(self) -> Inventory:
        """
        Get inventory from LiquidityManager contract.

        Implementation:
        1. Extract token0 and token1 from pair_address
        2. Check which token is registered using akAddressToPoolManager
        3. Use the registered token as akAddress
        4. Query akToStashedTokens for both token0 and token1
        5. If neither token is registered, exit with error

        Returns:
            Inventory with available amounts

        Raises:
            SystemExit: If no tokens are registered
        """
        # Step 1: Get pool tokens
        token0, token1 = await self._get_pool_tokens()

        # Step 2: Check which token is registered as AK
        ak_address = None

        registered_token0 = await self._find_registered_ak(token0)
        if registered_token0:
            ak_address = registered_token0
            logger.info(f"Using token0 ({token0}) as registered AK")
        else:
            registered_token1 = await self._find_registered_ak(token1)
            if registered_token1:
                ak_address = registered_token1
                logger.info(f"Using token1 ({token1}) as registered AK")

        # Step 3: Exit if neither token is registered
        if ak_address is None:
            error_msg = (
                f"Neither token0 ({token0}) nor token1 ({token1}) is registered "
                f"in LiquidityManager at {self.liq_manager.address}. "
                f"Cannot determine inventory. Exiting."
            )
            logger.error(error_msg)
            raise SystemExit(error_msg)

        # Step 4: Query stashed tokens for both token0 and token1
        amount0, amount1 = await asyncio.gather(
            self._get_stashed_tokens(ak_address, token0),
            self._get_stashed_tokens(ak_address, token1),
        )

        inventory = Inventory(amount0=str(amount0), amount1=str(amount1))

        logger.info(
            f"Retrieved inventory for pair {self.pool.address}: "
            f"amount0={amount0}, amount1={amount1}"
        )

        return inventory

    async def get_current_price(self) -> int:
        """Get the current price of the pool."""
        slot0 = await self.pool.functions.slot0().call()
        return slot0[0]

    async def get_current_positions(self) -> List[Position]:
        # 1. Create pool contract to get tokens
        token0, token1 = await asyncio.gather(
            self.pool.functions.token0().call(),
            self.pool.functions.token1().call(),
        )
        logger.debug(f"Pool tokens - Token0: {token0}, Token1: {token1}")
        # 3. Determine which token is the AK token (has position manager)
        position_manager_address_0, position_manager_address_1 = await asyncio.gather(
            self.liq_manager.functions.akAddressToPositionManager(
                Web3.to_checksum_address(token0)
            ).call(),
            self.liq_manager.functions.akAddressToPositionManager(
                Web3.to_checksum_address(token1)
            ).call(),
        )
        if (
            position_manager_address_0 != ZERO_ADDRESS
            and position_manager_address_1
            != ZERO_ADDRESS
        ):
            raise ValueError("Invalid vault")
        if position_manager_address_0 != ZERO_ADDRESS:
            position_manager_address = position_manager_address_0
            logger.debug(f"Token0 ({token0}) is the AK token")
        elif position_manager_address_1 != ZERO_ADDRESS:
            position_manager_address = position_manager_address_1
            logger.debug(f"Token1 ({token1}) is the AK token")
        else:
            raise ValueError(
                f"Neither token0 ({token0}) nor token1 ({token1}) maps to a PositionManager"
            )

        logger.debug(f"Position manager: {position_manager_address}")

        # Get current pool price for amount calculations
        current_sqrt_price_x96 = await self.get_current_price()

        # 4. Get token IDs from position manager
        pos_manager_contract = AsyncWeb3Helper.make_web3(chain_id=self.chain_id).make_contract_by_name(
            name="AeroCLPositionManager",
            addr=position_manager_address,
        )
        try:
            token_ids = await pos_manager_contract.functions.tokenIds().call()
        except Exception as e:
            logger.warning(f"No token ids found: {e}")
            return []

        logger.debug(f"Found {len(token_ids)} token IDs: {token_ids}")
        if not token_ids:
            return []

        # 5. Get NFT manager address
        nft_manager_address = await pos_manager_contract.functions.nftManager().call()
        logger.debug(f"NFT manager: {nft_manager_address}")

        # 6. Get position details for each token ID
        nft_manager_contract = AsyncWeb3Helper.make_web3(chain_id=self.chain_id).make_contract_by_name(
            name="INonfungiblePositionManager",
            addr=nft_manager_address,
        )

        positions = []
        for token_id in token_ids:
            try:
                position_info = await nft_manager_contract.functions.positions(
                    token_id
                ).call()

                # Position info: (nonce, operator, token0, token1, tickSpacing,
                #                 tickLower, tickUpper, liquidity, ...)
                tick_lower = position_info[5]
                tick_upper = position_info[6]
                liquidity = position_info[7]

                logger.debug(
                    f"Position {token_id}: ticks [{tick_lower}, {tick_upper}], "
                    f"liquidity {liquidity}"
                )

                # Convert liquidity to actual amounts based on current price
                amount0, amount1 = UniswapV3Math.get_amounts_for_liquidity(
                    current_sqrt_price_x96,
                    UniswapV3Math.get_sqrt_ratio_at_tick(tick_lower),
                    UniswapV3Math.get_sqrt_ratio_at_tick(tick_upper),
                    liquidity,
                )

                positions.append(
                    Position(
                        tick_lower=tick_lower,
                        tick_upper=tick_upper,
                        allocation0=str(amount0),
                        allocation1=str(amount1),
                    )
                )
            except Exception as e:
                logger.warning(f"Failed to read position {token_id}: {e}")
                continue

        return positions

"""
Inventory provider implementations for SN98 ForeverMoney.

This module provides different methods for obtaining token inventory
for LP strategy generation.
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import Optional, Tuple, Dict, List

from web3 import Web3, AsyncWeb3
from web3.contract import AsyncContract

from protocol import Inventory, Position

logger = logging.getLogger(__name__)


class SnLiqManager:
    """SnLiqManager"""

    def __init__(
        self,
        liquidity_manager_address: str,
        pool_address: str,
        w3: AsyncWeb3,
    ):
        """Initialize the LiquidityManager inventory provider."""
        # Initialize Web3
        self.w3: AsyncWeb3 = w3
        if not self.w3.is_connected():
            raise ConnectionError(f"Failed to connect to RPC")

        # Create contract instance
        self.liq_manager: AsyncContract = self.w3.eth.contract(
            address=Web3.to_checksum_address(liquidity_manager_address),
            abi=self._load_abi("LiquidityManager"),
        )
        self.pool: AsyncContract = self.w3.eth.contract(
            address=Web3.to_checksum_address(pool_address),
            abi=self._load_abi("ICLPool"),
        )

        logger.info(
            f"Initialized SnLiqManagerInventory with contract at {self.liq_manager.address}"
        )

    def _load_abi(self, abi_name: str) -> List[Dict]:
        """Load the abi."""
        abi_path = Path(__file__).parent.parent / "utils" / "abis" / f"{abi_name}.json"
        if not abi_path.exists():
            raise FileNotFoundError(f"ABI not found at {abi_path}")

        with open(abi_path, "r") as f:
            abi_data = json.load(f)
            if isinstance(abi_data, dict):
                return abi_data.get("abi", abi_data)
            return abi_data

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
            raise ValueError(f"Failed to extract tokens from pool {self.pool.address}: {e}")

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
            if pool_manager != "0x0000000000000000000000000000000000000000":
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
        (sqrt_price_x96,) = await self.pool.functions.slot0()
        return sqrt_price_x96

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
                self.w3.to_checksum_address(token0)
            ).call(),
            self.liq_manager.functions.akAddressToPositionManager(
                self.w3.to_checksum_address(token1)
            ).call(),
        )
        if (
            position_manager_address_0 != "0x0000000000000000000000000000000000000000"
            and position_manager_address_1
            != "0x0000000000000000000000000000000000000000"
        ):
            raise ValueError("Invalid vault")
        if position_manager_address_0 != "0x0000000000000000000000000000000000000000":
            position_manager_address = position_manager_address_0
            logger.debug(f"Token0 ({token0}) is the AK token")
        elif position_manager_address_1 != "0x0000000000000000000000000000000000000000":
            position_manager_address = position_manager_address_1
            logger.debug(f"Token1 ({token1}) is the AK token")
        else:
            raise ValueError(
                f"Neither token0 ({token0}) nor token1 ({token1}) maps to a PositionManager"
            )

        logger.debug(f"Position manager: {position_manager_address}")

        # 4. Get token IDs from position manager
        pos_manager_abi = self._load_abi("AeroCLPositionManager")
        pos_manager_contract = self.w3.eth.contract(
            address=self.w3.to_checksum_address(position_manager_address),
            abi=pos_manager_abi,
        )
        token_ids = await pos_manager_contract.functions.tokenIds().call()
        logger.debug(f"Found {len(token_ids)} token IDs: {token_ids}")

        if not token_ids:
            return []

        # 5. Get NFT manager address
        nft_manager_address = await pos_manager_contract.functions.nftManager().call()
        logger.debug(f"NFT manager: {nft_manager_address}")

        # 6. Get position details for each token ID
        nft_manager_abi = self._load_abi("INonfungiblePositionManager")
        nft_manager_contract = self.w3.eth.contract(
            address=self.w3.to_checksum_address(nft_manager_address),
            abi=nft_manager_abi,
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

                # Convert liquidity to amounts (simplified - using liquidity as allocation)
                # In production, you'd calculate proper token amounts based on liquidity and ticks
                positions.append(
                    Position(
                        tick_lower=tick_lower,
                        tick_upper=tick_upper,
                        allocation0=str(liquidity // 2),  # Simplified allocation
                        allocation1=str(liquidity // 2),  # Simplified allocation
                    )
                )
            except Exception as e:
                logger.warning(f"Failed to read position {token_id}: {e}")
                continue

        return positions

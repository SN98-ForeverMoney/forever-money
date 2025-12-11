"""
Inventory provider implementations for SN98 ForeverMoney.

This module provides different methods for obtaining token inventory
for LP strategy generation.
"""
import json
import logging
from abc import ABC, abstractmethod
from typing import Optional, Tuple
from pathlib import Path

from web3 import Web3
from web3.contract import Contract
from protocol import Inventory

logger = logging.getLogger(__name__)


class InventoryProvider(ABC):
    """
    Base class for inventory providers.

    Inventory providers determine the available token amounts for
    deploying LP strategies.
    """

    @abstractmethod
    def get_inventory(self, pair_address: str, chain_id: int = 8453) -> Inventory:
        """
        Get the available inventory for a given pair.

        Args:
            pair_address: The address of the trading pair
            chain_id: The blockchain ID (default: Base = 8453)

        Returns:
            Inventory object with amount0 and amount1

        Raises:
            ValueError: If inventory cannot be determined
        """
        pass


class SnLiqManagerInventory(InventoryProvider):
    """
    Inventory provider using the LiquidityManager contract.

    This implementation:
    1. Given a pair_address, extracts token0 and token1
    2. Checks akAddressToPoolManager for each token to find the registered AK
    3. Uses akToStashedTokens[akAddress][token] to get the stashed inventory
    4. If both tokens fail akAddressToPoolManager, exits the program

    Usage:
        provider = SnLiqManagerInventory(
            liquidity_manager_address="0x...",
            rpc_url="https://mainnet.base.org",
            pool_factory_address="0x..."  # UniswapV3 pool factory
        )
        inventory = provider.get_inventory(pair_address="0x...")
    """

    def __init__(
        self,
        liquidity_manager_address: str,
        rpc_url: str,
        pool_factory_address: Optional[str] = None,
        abi_path: Optional[str] = None
    ):
        """
        Initialize the LiquidityManager inventory provider.

        Args:
            liquidity_manager_address: Address of the LiquidityManager contract
            rpc_url: RPC endpoint URL for blockchain connection
            pool_factory_address: Optional address of pool factory for token extraction
            abi_path: Optional path to LiquidityManager ABI JSON file
        """
        self.liquidity_manager_address = Web3.to_checksum_address(liquidity_manager_address)
        self.rpc_url = rpc_url
        self.pool_factory_address = pool_factory_address

        # Initialize Web3
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        if not self.w3.is_connected():
            raise ConnectionError(f"Failed to connect to RPC at {rpc_url}")

        # Load LiquidityManager ABI
        if abi_path is None:
            # Default to the ABI in utils/abis/
            abi_path = Path(__file__).parent.parent / "utils" / "abis" / "LiquidityManager.json"

        with open(abi_path, 'r') as f:
            abi_data = json.load(f)
            # Handle both raw ABI array and full artifact with 'abi' key
            if isinstance(abi_data, list):
                self.liq_manager_abi = abi_data
            else:
                self.liq_manager_abi = abi_data.get('abi', abi_data)

        # Create contract instance
        self.liq_manager: Contract = self.w3.eth.contract(
            address=self.liquidity_manager_address,
            abi=self.liq_manager_abi
        )

        logger.info(f"Initialized SnLiqManagerInventory with contract at {self.liquidity_manager_address}")

    def _get_pool_tokens(self, pair_address: str) -> Tuple[str, str]:
        """
        Extract token0 and token1 addresses from a pool.

        Args:
            pair_address: The pool/pair address

        Returns:
            Tuple of (token0_address, token1_address)

        Raises:
            ValueError: If tokens cannot be extracted
        """
        try:
            # Standard UniswapV3 pool interface
            pool_abi = [
                {
                    "inputs": [],
                    "name": "token0",
                    "outputs": [{"internalType": "address", "name": "", "type": "address"}],
                    "stateMutability": "view",
                    "type": "function"
                },
                {
                    "inputs": [],
                    "name": "token1",
                    "outputs": [{"internalType": "address", "name": "", "type": "address"}],
                    "stateMutability": "view",
                    "type": "function"
                }
            ]

            pool_contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(pair_address),
                abi=pool_abi
            )

            token0 = pool_contract.functions.token0().call()
            token1 = pool_contract.functions.token1().call()

            logger.info(f"Extracted tokens from pool {pair_address}: token0={token0}, token1={token1}")
            return (token0, token1)

        except Exception as e:
            raise ValueError(f"Failed to extract tokens from pool {pair_address}: {e}")

    def _find_registered_ak(self, token_address: str) -> Optional[str]:
        """
        Check if a token is registered as an AK using akAddressToPoolManager.

        Args:
            token_address: The token address to check

        Returns:
            The token address if registered, None if reverts
        """
        try:
            # Call akAddressToPoolManager
            pool_manager = self.liq_manager.functions.akAddressToPoolManager(
                Web3.to_checksum_address(token_address)
            ).call()

            # If it returns a non-zero address, the token is registered
            if pool_manager != "0x0000000000000000000000000000000000000000":
                logger.info(f"Token {token_address} is registered with PoolManager {pool_manager}")
                return token_address
            else:
                logger.info(f"Token {token_address} returned zero address - not registered")
                return None

        except Exception as e:
            logger.info(f"Token {token_address} not registered (call reverted): {e}")
            return None

    def _get_stashed_tokens(self, ak_address: str, token_address: str) -> int:
        """
        Get stashed token amount using akToStashedTokens.

        Args:
            ak_address: The registered AK address
            token_address: The token address to query

        Returns:
            Amount of stashed tokens (in wei)
        """
        try:
            amount = self.liq_manager.functions.akToStashedTokens(
                Web3.to_checksum_address(ak_address),
                Web3.to_checksum_address(token_address)
            ).call()

            logger.info(f"Stashed tokens for AK {ak_address}, token {token_address}: {amount}")
            return amount

        except Exception as e:
            logger.warning(f"Failed to query stashed tokens for {ak_address}/{token_address}: {e}")
            return 0

    def get_inventory(self, pair_address: str, chain_id: int = 8453) -> Inventory:
        """
        Get inventory from LiquidityManager contract.

        Implementation:
        1. Extract token0 and token1 from pair_address
        2. Check which token is registered using akAddressToPoolManager
        3. Use the registered token as akAddress
        4. Query akToStashedTokens for both token0 and token1
        5. If neither token is registered, exit with error

        Args:
            pair_address: The pool/pair address
            chain_id: The blockchain ID

        Returns:
            Inventory with available amounts

        Raises:
            SystemExit: If no tokens are registered
        """
        # Step 1: Get pool tokens
        token0, token1 = self._get_pool_tokens(pair_address)

        # Step 2: Check which token is registered as AK
        ak_address = None

        registered_token0 = self._find_registered_ak(token0)
        if registered_token0:
            ak_address = registered_token0
            logger.info(f"Using token0 ({token0}) as registered AK")
        else:
            registered_token1 = self._find_registered_ak(token1)
            if registered_token1:
                ak_address = registered_token1
                logger.info(f"Using token1 ({token1}) as registered AK")

        # Step 3: Exit if neither token is registered
        if ak_address is None:
            error_msg = (
                f"Neither token0 ({token0}) nor token1 ({token1}) is registered "
                f"in LiquidityManager at {self.liquidity_manager_address}. "
                f"Cannot determine inventory. Exiting."
            )
            logger.error(error_msg)
            raise SystemExit(error_msg)

        # Step 4: Query stashed tokens for both token0 and token1
        amount0 = self._get_stashed_tokens(ak_address, token0)
        amount1 = self._get_stashed_tokens(ak_address, token1)

        inventory = Inventory(
            amount0=str(amount0),
            amount1=str(amount1)
        )

        logger.info(
            f"Retrieved inventory for pair {pair_address}: "
            f"amount0={amount0}, amount1={amount1}"
        )

        return inventory


class StaticInventory(InventoryProvider):
    """
    Simple static inventory provider for testing.

    Returns a fixed inventory regardless of pair address.
    """

    def __init__(self, amount0: int, amount1: int):
        """
        Initialize with fixed amounts.

        Args:
            amount0: Amount of token0 (in wei)
            amount1: Amount of token1 (in wei)
        """
        self.amount0 = amount0
        self.amount1 = amount1

    def get_inventory(self, pair_address: str, chain_id: int = 8453) -> Inventory:
        """
        Return the static inventory.

        Args:
            pair_address: Ignored
            chain_id: Ignored

        Returns:
            Static inventory
        """
        return Inventory(
            amount0=str(self.amount0),
            amount1=str(self.amount1)
        )

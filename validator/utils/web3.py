import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional

import web3
from web3 import AsyncHTTPProvider, AsyncWeb3, Web3
from web3.contract import Contract, AsyncContract

from validator.utils.env import (
    MAINNET_RPC,
    BASE_RPC,
)

logger = logging.getLogger(__name__)

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
DEFAULT_ABI_PATH = Path(__file__).parent / "abis"
CHAIN_ID_TO_RPC = {
    1: MAINNET_RPC,
    8453: BASE_RPC,
}

# Reuse one Web3 instance per chain to avoid 429 (too many requests) on public RPCs
_web3_cache: Dict[int, "AsyncWeb3Helper"] = {}


class AsyncWeb3Helper:
    """Class acting as web3 base class"""

    def __init__(self) -> None:
        """Initialize web3 helper"""
        self.web3: Optional[AsyncWeb3] = None

    @classmethod
    def make_web3(cls, chain_id: int) -> "AsyncWeb3Helper":
        if chain_id not in CHAIN_ID_TO_RPC:
            raise ValueError(f"Invalid chain id {chain_id}")
        if chain_id in _web3_cache:
            return _web3_cache[chain_id]
        instance = AsyncWeb3Helper()
        instance.web3 = AsyncWeb3(AsyncHTTPProvider(CHAIN_ID_TO_RPC[chain_id]))
        _web3_cache[chain_id] = instance
        logger.debug("Created cached AsyncWeb3Helper for chain_id=%s", chain_id)
        return instance

    def load_abi(self, path: Path) -> Dict[str, Any]:
        """Load an ABI file"""
        if not path.is_file():
            raise ValueError(f"Invalid ABI file path {path}")

        with open(path, "r") as f:
            abi_data = json.load(f)
            if isinstance(abi_data, dict):
                return abi_data.get("abi", abi_data)
            return abi_data

    def make_contract(self, abi_path: Path, addr: str) -> AsyncContract:
        """Make a contract object"""
        if self.web3 is None:
            raise ValueError("Web3 not initialized")
        abi = self.load_abi(abi_path)
        contract = self.web3.eth.contract(address=Web3.to_checksum_address(addr), abi=abi)
        return contract

    def make_contract_by_name(self, name: str, addr: str) -> AsyncContract:
        """Make a contract object"""
        abi_path = DEFAULT_ABI_PATH / f"{name}.json"
        contract = self.make_contract(abi_path, addr)
        return contract
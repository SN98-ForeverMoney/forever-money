"""
Vault Service for SN98 ForeverMoney Validator.

Handles business logic for miner-owned vault management including:
- Vault registration and verification
- Balance checking and snapshot management
- Eligibility filtering for evaluations
"""
import logging
from typing import List, Optional, Set
from decimal import Decimal
from datetime import datetime, timezone, timedelta

from web3 import Web3

from validator.repositories.vault import VaultRepository
from validator.models.miner_vault import MinerVault, VaultSnapshot
from validator.utils.web3 import AsyncWeb3Helper, ZERO_ADDRESS
from validator.utils.crypto import ss58_to_bytes32, is_valid_ss58
from validator.services.price import PriceService

logger = logging.getLogger(__name__)


class VaultService:
    """
    Service for managing miner-owned vaults.

    Provides vault registration, verification, balance tracking,
    and eligibility filtering for evaluations.
    """

    def __init__(
        self,
        vault_repository: Optional[VaultRepository] = None,
        price_service: Optional[PriceService] = None,
        default_minimum_usd: Decimal = Decimal("1000.00"),
    ):
        """
        Initialize the vault service.

        Args:
            vault_repository: Repository for vault database operations
            price_service: Service for getting token prices
            default_minimum_usd: Default minimum balance requirement
        """
        self.vault_repository = vault_repository or VaultRepository()
        self.price_service = price_service
        self.default_minimum_usd = default_minimum_usd

    async def register_miner_vault(
        self,
        miner_uid: int,
        miner_hotkey: str,
        vault_address: str,
        chain_id: int = 8453,
        auto_verify: bool = True,
    ) -> MinerVault:
        """
        Register a vault for a miner and optionally verify ownership.

        Verification checks:
        1. The vault's associatedMiner() matches the miner's hotkey (bytes32 encoded)

        Args:
            miner_uid: Miner's UID
            miner_hotkey: Miner's hotkey
            vault_address: LiquidityManager contract address
            chain_id: Blockchain chain ID
            auto_verify: If True, attempt to verify ownership immediately

        Returns:
            Registered MinerVault object
        """
        vault = await self.vault_repository.register_vault(
            miner_uid=miner_uid,
            miner_hotkey=miner_hotkey,
            vault_address=vault_address,
            chain_id=chain_id,
            minimum_balance_usd=self.default_minimum_usd,
        )

        if auto_verify:
            is_associated = await self.verify_associated_miner(
                vault_address=vault_address,
                expected_miner_hotkey=miner_hotkey,
                chain_id=chain_id,
            )
            if is_associated:
                await self.vault_repository.verify_vault(vault_address)
                vault.is_verified = True
                logger.info(f"Auto-verified vault {vault_address} for miner {miner_uid}")
            else:
                logger.warning(
                    f"Could not verify associatedMiner of vault {vault_address} for miner {miner_uid}"
                )

        return vault

    async def verify_associated_miner(
        self,
        vault_address: str,
        expected_miner_hotkey: str,
        chain_id: int = 8453,
    ) -> bool:
        """
        Verify that the vault's associatedMiner() matches the expected miner hotkey.

        The associatedMiner() function returns a bytes32 encoded miner address.
        This is the primary verification method for miner-owned vaults.

        Args:
            vault_address: LiquidityManager contract address
            expected_miner_hotkey: Expected miner hotkey (SS58 or hex address)
            chain_id: Blockchain chain ID

        Returns:
            True if associatedMiner matches, False otherwise
        """
        try:
            web3_helper = AsyncWeb3Helper.make_web3(chain_id)
            contract = web3_helper.make_contract_by_name(
                name="LiquidityManager",
                addr=vault_address,
            )

            # Call associatedMiner() - returns bytes32
            associated_miner_bytes32 = await contract.functions.associatedMiner().call()

            # Convert bytes32 to hex string for comparison
            if isinstance(associated_miner_bytes32, bytes):
                associated_miner_hex = associated_miner_bytes32.hex()
            else:
                associated_miner_hex = str(associated_miner_bytes32)

            # Normalize the expected hotkey for comparison
            # The hotkey could be SS58 format or already hex
            expected_normalized = self._normalize_hotkey_to_bytes32(expected_miner_hotkey)

            # Compare (case-insensitive)
            matches = associated_miner_hex.lower() == expected_normalized.lower()

            if matches:
                logger.info(
                    f"associatedMiner verified for vault {vault_address}: {associated_miner_hex}"
                )
            else:
                logger.warning(
                    f"associatedMiner mismatch for vault {vault_address}: "
                    f"expected {expected_normalized}, got {associated_miner_hex}"
                )

            return matches

        except Exception as e:
            logger.error(f"Error verifying associatedMiner for vault {vault_address}: {e}")
            return False

    def _normalize_hotkey_to_bytes32(self, hotkey: str) -> str:
        """
        Normalize a hotkey to bytes32 hex format for comparison.

        Handles:
        - SS58 addresses (Bittensor/Polkadot format) - properly decoded to AccountId32
        - Hex strings (with or without 0x prefix)

        Args:
            hotkey: Miner hotkey (SS58 format or hex)

        Returns:
            Bytes32 hex string (without 0x prefix)
        """
        # Remove 0x prefix if present
        if hotkey.startswith("0x"):
            hotkey = hotkey[2:]

        # If it's already 64 hex chars (32 bytes), return as-is
        if len(hotkey) == 64 and all(c in "0123456789abcdefABCDEF" for c in hotkey):
            return hotkey.lower()

        # Try to decode as SS58 address
        if is_valid_ss58(hotkey):
            try:
                account_id = ss58_to_bytes32(hotkey)
                return account_id.hex()
            except ValueError as e:
                logger.warning(f"Failed to decode SS58 address {hotkey}: {e}")

        # Fallback: try to decode as hex
        try:
            if all(c in "0123456789abcdefABCDEF" for c in hotkey):
                # Pad or truncate to 32 bytes
                hotkey_bytes = bytes.fromhex(hotkey)
                padded = hotkey_bytes.ljust(32, b'\x00')[:32]
                return padded.hex()
        except Exception as e:
            logger.warning(f"Could not normalize hotkey {hotkey}: {e}")

        return hotkey.lower()

    async def verify_vault_ownership(
        self,
        vault_address: str,
        expected_owner: str,
        chain_id: int = 8453,
    ) -> bool:
        """
        Verify that a miner owns a vault by checking the contract's owner.

        This is a simplified ownership check that reads the owner() function
        from the LiquidityManager contract. For production, you may want to
        add signature-based verification (EIP-712/EIP-1271).

        Args:
            vault_address: LiquidityManager contract address
            expected_owner: Expected owner address (miner's hotkey)
            chain_id: Blockchain chain ID

        Returns:
            True if ownership verified, False otherwise
        """
        try:
            web3_helper = AsyncWeb3Helper.make_web3(chain_id)
            contract = web3_helper.make_contract_by_name(
                name="LiquidityManager",
                addr=vault_address,
            )

            # Try to call owner() function
            try:
                owner = await contract.functions.owner().call()
                owner_lower = owner.lower()
                expected_lower = expected_owner.lower()

                # Check if owner matches expected
                if owner_lower == expected_lower:
                    logger.info(f"Ownership verified: {vault_address} owned by {owner}")
                    return True

                # Also check if it's a multisig and the miner is a signer
                # For now, just do direct comparison
                logger.info(
                    f"Ownership mismatch for {vault_address}: "
                    f"expected {expected_owner}, got {owner}"
                )
                return False

            except Exception as e:
                logger.warning(f"Could not call owner() on {vault_address}: {e}")
                # If no owner() function, we can't verify ownership this way
                return False

        except Exception as e:
            logger.error(f"Error verifying vault ownership: {e}")
            return False

    async def check_vault_balance(
        self,
        vault: MinerVault,
        pool_address: str,
    ) -> Optional[VaultSnapshot]:
        """
        Check and record the current balance of a vault.

        Args:
            vault: MinerVault to check
            pool_address: Associated pool address for token info

        Returns:
            VaultSnapshot if successful, None if failed
        """
        try:
            web3_helper = AsyncWeb3Helper.make_web3(vault.chain_id)
            liq_manager = web3_helper.make_contract_by_name(
                name="LiquidityManager",
                addr=vault.vault_address,
            )
            pool = web3_helper.make_contract_by_name(
                name="ICLPool",
                addr=pool_address,
            )

            # Get pool tokens
            token0 = await pool.functions.token0().call()
            token1 = await pool.functions.token1().call()

            # Find registered AK token
            ak_address = None
            for token in [token0, token1]:
                try:
                    pool_manager = await liq_manager.functions.akAddressToPoolManager(
                        Web3.to_checksum_address(token)
                    ).call()
                    if pool_manager != ZERO_ADDRESS:
                        ak_address = token
                        break
                except Exception:
                    continue

            if ak_address is None:
                logger.warning(f"No registered AK token found for vault {vault.vault_address}")
                return None

            # Get stashed token amounts
            token0_balance = await liq_manager.functions.akToStashedTokens(
                Web3.to_checksum_address(ak_address),
                Web3.to_checksum_address(token0),
            ).call()

            token1_balance = await liq_manager.functions.akToStashedTokens(
                Web3.to_checksum_address(ak_address),
                Web3.to_checksum_address(token1),
            ).call()

            # Get current block
            if web3_helper.web3 is None:
                logger.error("AsyncWeb3Helper.web3 is not initialized; cannot record vault snapshot")
                return None
            current_block = await web3_helper.web3.eth.block_number

            # Calculate USD value (simplified - in production use price service)
            total_value_usd = await self._calculate_vault_value_usd(
                token0, token0_balance,
                token1, token1_balance,
                vault.chain_id,
            )

            # Save snapshot
            snapshot = await self.vault_repository.save_vault_snapshot(
                vault=vault,
                token0_balance=token0_balance,
                token1_balance=token1_balance,
                total_value_usd=total_value_usd,
                block_number=current_block,
            )

            logger.info(
                f"Vault {vault.vault_address} balance: "
                f"token0={token0_balance}, token1={token1_balance}, "
                f"value=${total_value_usd}"
            )

            return snapshot

        except Exception as e:
            logger.error(f"Error checking vault balance: {e}")
            return None

    async def _calculate_vault_value_usd(
        self,
        token0_address: str,
        token0_balance: int,
        token1_address: str,
        token1_balance: int,
        chain_id: int,
    ) -> Decimal:
        """
        Calculate the USD value of vault holdings.

        Args:
            token0_address: Token0 contract address
            token0_balance: Token0 balance in wei
            token1_address: Token1 contract address
            token1_balance: Token1 balance in wei
            chain_id: Blockchain chain ID

        Returns:
            Total value in USD
        """
        if self.price_service:
            try:
                # Get prices from price service
                price0 = await self.price_service.get_token_price(token0_address, chain_id)
                price1 = await self.price_service.get_token_price(token1_address, chain_id)

                # Assume 18 decimals for simplicity (should check actual decimals)
                value0 = Decimal(str(token0_balance)) / Decimal("1e18") * Decimal(str(price0))
                value1 = Decimal(str(token1_balance)) / Decimal("1e18") * Decimal(str(price1))

                return value0 + value1
            except Exception as e:
                logger.warning(f"Could not get token prices: {e}")

        # Fallback: estimate based on balance (very rough)
        # In production, always use actual prices
        total_balance = token0_balance + token1_balance
        # Assume ~$1 per token for estimation (placeholder)
        estimated_value = Decimal(str(total_balance)) / Decimal("1e18")
        return estimated_value

    async def is_miner_eligible_for_evaluation(
        self,
        miner_uid: int,
        require_verified: bool = True,
        require_minimum_balance: bool = True,
    ) -> bool:
        """
        Check if a miner is eligible to participate in evaluations.

        Args:
            miner_uid: Miner's UID
            require_verified: Require vault to be verified
            require_minimum_balance: Require vault to meet minimum balance

        Returns:
            True if eligible, False otherwise
        """
        # Get miner's verified vault
        if require_verified:
            vault = await self.vault_repository.get_verified_vault_by_miner(miner_uid)
        else:
            vaults = await self.vault_repository.get_vaults_by_miner(miner_uid)
            vault = vaults[0] if vaults else None

        if vault is None:
            logger.debug(f"Miner {miner_uid} has no registered vault")
            return False

        # Check minimum balance if required
        if require_minimum_balance:
            meets_minimum = await self.vault_repository.check_vault_meets_minimum(vault)
            if not meets_minimum:
                logger.debug(f"Miner {miner_uid} vault does not meet minimum balance")
                return False

        return True

    async def filter_eligible_miners(
        self,
        miner_uids: List[int],
        require_verified: bool = True,
        require_minimum_balance: bool = True,
    ) -> List[int]:
        """
        Filter a list of miners to only those with eligible vaults.

        Args:
            miner_uids: List of miner UIDs to filter
            require_verified: Require vaults to be verified
            require_minimum_balance: Require vaults to meet minimum balance

        Returns:
            List of eligible miner UIDs
        """
        eligible_uids = []

        for uid in miner_uids:
            if await self.is_miner_eligible_for_evaluation(
                uid, require_verified, require_minimum_balance
            ):
                eligible_uids.append(uid)

        logger.info(
            f"Filtered {len(miner_uids)} miners to {len(eligible_uids)} with eligible vaults"
        )
        return eligible_uids

    async def get_eligible_miner_set(self) -> Set[int]:
        """
        Get a set of all miner UIDs with eligible vaults.

        Returns:
            Set of eligible miner UIDs
        """
        eligible_uids = await self.vault_repository.get_eligible_miner_uids(
            self.default_minimum_usd
        )
        return set(eligible_uids)

    async def get_vault_performance_score(
        self,
        vault: MinerVault,
        lookback_hours: int = 24,
    ) -> float:
        """
        Calculate a performance score based on vault balance changes.

        This is a simple metric that can be incorporated into miner scoring.

        Args:
            vault: MinerVault to evaluate
            lookback_hours: Hours to look back for comparison

        Returns:
            Performance score (positive = growth, negative = loss)
        """
        latest = await self.vault_repository.get_latest_snapshot(vault)
        if latest is None:
            return 0.0

        # Get snapshot from lookback period
        lookback_time = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        old_snapshot = await VaultSnapshot.filter(
            vault=vault,
            snapshot_at__lte=lookback_time,
        ).order_by("-snapshot_at").first()

        if old_snapshot is None:
            # No historical data, return neutral
            return 0.0

        # Calculate percentage change
        if old_snapshot.total_value_usd == 0:
            return 0.0

        change = (
            float(latest.total_value_usd) - float(old_snapshot.total_value_usd)
        ) / float(old_snapshot.total_value_usd)

        return change

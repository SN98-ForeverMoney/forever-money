"""
Async Vault Repository for SN98 ForeverMoney Validator.

Handles all database operations for miner-owned vaults.
Uses Tortoise ORM - all methods are async.
"""
import logging
from typing import List, Optional
from datetime import datetime, timezone
from decimal import Decimal

from validator.models.miner_vault import MinerVault, VaultSnapshot
from validator.utils.env import MINIMUM_VAULT_BALANCE_USD

logger = logging.getLogger(__name__)


class VaultRepository:
    """Async vault repository using Tortoise ORM."""

    async def register_vault(
        self,
        miner_uid: int,
        miner_hotkey: str,
        vault_address: str,
        chain_id: int = 8453,
        minimum_balance_usd: Decimal = MINIMUM_VAULT_BALANCE_USD,
    ) -> MinerVault:
        """
        Register a new miner vault.

        Args:
            miner_uid: Miner's UID in the subnet
            miner_hotkey: Miner's hotkey address
            vault_address: LiquidityManager contract address
            chain_id: Blockchain chain ID (default: Base L2)
            minimum_balance_usd: Minimum required balance in USD

        Returns:
            Created MinerVault object
        """
        vault, created = await MinerVault.get_or_create(
            vault_address=vault_address.lower(),
            defaults={
                "miner_uid": miner_uid,
                "miner_hotkey": miner_hotkey,
                "chain_id": chain_id,
                "minimum_balance_usd": minimum_balance_usd,
                "is_verified": False,
                "is_active": True,
            },
        )

        if created:
            logger.info(f"Registered new vault {vault_address} for miner {miner_uid}")
        else:
            # Update vault metadata if it already exists
            vault.miner_uid = miner_uid
            vault.miner_hotkey = miner_hotkey
            vault.chain_id = chain_id
            vault.minimum_balance_usd = minimum_balance_usd
            vault.is_active = True
            await vault.save()
            logger.info(f"Updated vault {vault_address} for miner {miner_uid}")

        return vault

    async def get_vault_by_address(self, vault_address: str) -> Optional[MinerVault]:
        """
        Get a vault by its contract address.

        Args:
            vault_address: LiquidityManager contract address

        Returns:
            MinerVault object or None if not found
        """
        return await MinerVault.get_or_none(vault_address=vault_address.lower())

    async def get_vaults_by_miner(self, miner_uid: int) -> List[MinerVault]:
        """
        Get all vaults registered by a miner.

        Args:
            miner_uid: Miner's UID

        Returns:
            List of MinerVault objects
        """
        return await MinerVault.filter(miner_uid=miner_uid, is_active=True).all()

    async def get_verified_vault_by_miner(self, miner_uid: int) -> Optional[MinerVault]:
        """
        Get the verified and active vault for a miner.

        Args:
            miner_uid: Miner's UID

        Returns:
            MinerVault object or None if no verified vault
        """
        return await MinerVault.filter(
            miner_uid=miner_uid, is_verified=True, is_active=True
        ).first()

    async def get_all_verified_vaults(self) -> List[MinerVault]:
        """
        Get all verified and active vaults.

        Returns:
            List of verified MinerVault objects
        """
        return await MinerVault.filter(is_verified=True, is_active=True).all()

    async def verify_vault(self, vault_address: str) -> Optional[MinerVault]:
        """
        Mark a vault as verified.

        Args:
            vault_address: LiquidityManager contract address

        Returns:
            Updated MinerVault object or None if not found
        """
        vault = await self.get_vault_by_address(vault_address)
        if vault:
            vault.is_verified = True
            vault.verified_at = datetime.now(timezone.utc)
            await vault.save()
            logger.info(f"Verified vault {vault_address}")
        return vault

    async def deactivate_vault(self, vault_address: str) -> Optional[MinerVault]:
        """
        Deactivate a vault (e.g., when balance drops below minimum).

        Args:
            vault_address: LiquidityManager contract address

        Returns:
            Updated MinerVault object or None if not found
        """
        vault = await self.get_vault_by_address(vault_address)
        if vault:
            vault.is_active = False
            await vault.save()
            logger.info(f"Deactivated vault {vault_address}")
        return vault

    async def save_vault_snapshot(
        self,
        vault: MinerVault,
        token0_balance: int,
        token1_balance: int,
        total_value_usd: Decimal,
        block_number: int,
    ) -> VaultSnapshot:
        """
        Save a balance snapshot for a vault.

        Args:
            vault: MinerVault object
            token0_balance: Token0 balance in wei
            token1_balance: Token1 balance in wei
            total_value_usd: Total value in USD
            block_number: Block number at snapshot time

        Returns:
            Created VaultSnapshot object
        """
        snapshot = await VaultSnapshot.create(
            vault=vault,
            token0_balance=Decimal(str(token0_balance)),
            token1_balance=Decimal(str(token1_balance)),
            total_value_usd=total_value_usd,
            block_number=block_number,
        )

        # Update last balance check timestamp on vault
        vault.last_balance_check = datetime.now(timezone.utc)
        await vault.save()

        logger.debug(
            f"Saved snapshot for vault {vault.vault_address}: "
            f"${total_value_usd} at block {block_number}"
        )
        return snapshot

    async def get_latest_snapshot(self, vault: MinerVault) -> Optional[VaultSnapshot]:
        """
        Get the most recent balance snapshot for a vault.

        Args:
            vault: MinerVault object

        Returns:
            Latest VaultSnapshot or None
        """
        return await VaultSnapshot.filter(vault=vault).order_by("-snapshot_at").first()

    async def check_vault_meets_minimum(
        self, vault: MinerVault, minimum_usd: Optional[Decimal] = None
    ) -> bool:
        """
        Check if a vault's latest balance meets the minimum requirement.

        Args:
            vault: MinerVault object
            minimum_usd: Override minimum (uses vault's default if not provided)

        Returns:
            True if vault meets minimum, False otherwise
        """
        minimum = minimum_usd or vault.minimum_balance_usd
        snapshot = await self.get_latest_snapshot(vault)

        if snapshot is None:
            logger.warning(f"No snapshot found for vault {vault.vault_address}")
            return False

        meets_minimum = snapshot.total_value_usd >= minimum
        if not meets_minimum:
            logger.info(
                f"Vault {vault.vault_address} balance ${snapshot.total_value_usd} "
                f"below minimum ${minimum}"
            )
        return meets_minimum

    async def get_eligible_miner_uids(self, minimum_usd: Optional[Decimal] = None) -> List[int]:
        """
        Get UIDs of all miners with verified vaults meeting minimum balance.

        Args:
            minimum_usd: Optional minimum balance override

        Returns:
            List of eligible miner UIDs
        """
        verified_vaults = await self.get_all_verified_vaults()
        eligible_uids = []

        for vault in verified_vaults:
            if await self.check_vault_meets_minimum(vault, minimum_usd):
                eligible_uids.append(vault.miner_uid)

        logger.info(f"Found {len(eligible_uids)} miners with eligible vaults")
        return eligible_uids

    async def get_registered_miner_uids(self) -> List[int]:
        """
        Get UIDs of all miners who have any vault registered (regardless of verification status).

        This is used to identify miners who haven't registered yet, so we can
        query them for their vault info.

        Returns:
            List of miner UIDs with registered vaults
        """
        vaults = await MinerVault.filter(is_active=True).all()
        return [vault.miner_uid for vault in vaults]

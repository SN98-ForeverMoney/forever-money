"""
Tests for Miner-Owned Vaults functionality.

Tests vault registration, verification, balance tracking,
and eligibility filtering for evaluations.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from decimal import Decimal
from datetime import datetime, timezone, timedelta

from validator.models.miner_vault import MinerVault, VaultSnapshot
from validator.repositories.vault import VaultRepository
from validator.services.vault import VaultService


class TestVaultRepository:
    """Tests for VaultRepository using mocks."""

    @pytest.mark.asyncio
    async def test_register_vault_new(self):
        """Test registering a new vault."""
        with patch.object(MinerVault, 'get_or_create', new_callable=AsyncMock) as mock_get_or_create:
            mock_vault = MagicMock(spec=MinerVault)
            mock_vault.miner_uid = 20
            mock_vault.is_active = True
            mock_vault.is_verified = False
            mock_get_or_create.return_value = (mock_vault, True)

            repo = VaultRepository()
            vault = await repo.register_vault(
                miner_uid=20,
                miner_hotkey="0x2034567890123456789012345678901234567890",
                vault_address="0xrepo1234567890abcdef1234567890abcdef",
                chain_id=8453,
                minimum_balance_usd=Decimal("500.00"),
            )

            assert vault.miner_uid == 20
            assert vault.is_active is True
            assert vault.is_verified is False
            mock_get_or_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_vault_by_address(self):
        """Test getting a vault by address."""
        with patch.object(MinerVault, 'get_or_none', new_callable=AsyncMock) as mock_get:
            mock_vault = MagicMock(spec=MinerVault)
            mock_vault.miner_uid = 30
            mock_get.return_value = mock_vault

            repo = VaultRepository()
            vault = await repo.get_vault_by_address("0xTEST1234")

            assert vault is not None
            assert vault.miner_uid == 30
            # Check that lowercase was applied
            mock_get.assert_called_once_with(vault_address="0xtest1234")

    @pytest.mark.asyncio
    async def test_verify_vault(self):
        """Test verifying a vault."""
        with patch.object(VaultRepository, 'get_vault_by_address', new_callable=AsyncMock) as mock_get:
            mock_vault = MagicMock(spec=MinerVault)
            mock_vault.is_verified = False
            mock_vault.save = AsyncMock()
            mock_get.return_value = mock_vault

            repo = VaultRepository()
            vault = await repo.verify_vault("0xverify1234")

            assert vault.is_verified is True
            assert vault.verified_at is not None
            mock_vault.save.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_verified_vault_by_miner(self):
        """Test getting verified vault for a miner."""
        with patch.object(MinerVault, 'filter') as mock_filter:
            mock_vault = MagicMock(spec=MinerVault)
            mock_vault.is_verified = True
            mock_filter.return_value.first = AsyncMock(return_value=mock_vault)

            repo = VaultRepository()
            vault = await repo.get_verified_vault_by_miner(50)

            assert vault is not None
            assert vault.is_verified is True
            mock_filter.assert_called_once_with(
                miner_uid=50, is_verified=True, is_active=True
            )

    @pytest.mark.asyncio
    async def test_save_vault_snapshot(self):
        """Test saving a vault snapshot."""
        mock_vault = MagicMock(spec=MinerVault)
        mock_vault.vault_address = "0xtest1234"
        mock_vault.save = AsyncMock()

        with patch.object(VaultSnapshot, 'create', new_callable=AsyncMock) as mock_create:
            mock_snapshot = MagicMock(spec=VaultSnapshot)
            mock_snapshot.total_value_usd = Decimal("3000.00")
            mock_create.return_value = mock_snapshot

            repo = VaultRepository()
            snapshot = await repo.save_vault_snapshot(
                vault=mock_vault,
                token0_balance=1000000000000000000,
                token1_balance=2000000000,
                total_value_usd=Decimal("3000.00"),
                block_number=100000,
            )

            assert snapshot.total_value_usd == Decimal("3000.00")
            mock_create.assert_called_once()
            mock_vault.save.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_vault_meets_minimum_no_snapshot(self):
        """Test checking minimum when no snapshot exists."""
        mock_vault = MagicMock(spec=MinerVault)
        mock_vault.vault_address = "0xtest1234"
        mock_vault.minimum_balance_usd = Decimal("1000.00")

        with patch.object(VaultRepository, 'get_latest_snapshot', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None

            repo = VaultRepository()
            meets_min = await repo.check_vault_meets_minimum(mock_vault)

            assert meets_min is False

    @pytest.mark.asyncio
    async def test_check_vault_meets_minimum_below(self):
        """Test checking minimum when balance is below threshold."""
        mock_vault = MagicMock(spec=MinerVault)
        mock_vault.vault_address = "0xtest1234"
        mock_vault.minimum_balance_usd = Decimal("1000.00")

        mock_snapshot = MagicMock(spec=VaultSnapshot)
        mock_snapshot.total_value_usd = Decimal("500.00")

        with patch.object(VaultRepository, 'get_latest_snapshot', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_snapshot

            repo = VaultRepository()
            meets_min = await repo.check_vault_meets_minimum(mock_vault)

            assert meets_min is False

    @pytest.mark.asyncio
    async def test_check_vault_meets_minimum_above(self):
        """Test checking minimum when balance is above threshold."""
        mock_vault = MagicMock(spec=MinerVault)
        mock_vault.minimum_balance_usd = Decimal("1000.00")

        mock_snapshot = MagicMock(spec=VaultSnapshot)
        mock_snapshot.total_value_usd = Decimal("2000.00")

        with patch.object(VaultRepository, 'get_latest_snapshot', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_snapshot

            repo = VaultRepository()
            meets_min = await repo.check_vault_meets_minimum(mock_vault)

            assert meets_min is True


class TestVaultService:
    """Tests for VaultService using mocks."""

    @pytest.mark.asyncio
    async def test_is_miner_eligible_no_vault(self):
        """Test eligibility check when miner has no vault."""
        mock_repo = MagicMock(spec=VaultRepository)
        mock_repo.get_verified_vault_by_miner = AsyncMock(return_value=None)

        service = VaultService(vault_repository=mock_repo)
        eligible = await service.is_miner_eligible_for_evaluation(
            miner_uid=999,
            require_verified=True,
            require_minimum_balance=True,
        )

        assert eligible is False
        mock_repo.get_verified_vault_by_miner.assert_called_once_with(999)

    @pytest.mark.asyncio
    async def test_is_miner_eligible_with_verified_vault(self):
        """Test eligibility with verified vault meeting minimum."""
        mock_vault = MagicMock(spec=MinerVault)

        mock_repo = MagicMock(spec=VaultRepository)
        mock_repo.get_verified_vault_by_miner = AsyncMock(return_value=mock_vault)
        mock_repo.check_vault_meets_minimum = AsyncMock(return_value=True)

        service = VaultService(vault_repository=mock_repo)
        eligible = await service.is_miner_eligible_for_evaluation(
            miner_uid=80,
            require_verified=True,
            require_minimum_balance=True,
        )

        assert eligible is True
        mock_repo.get_verified_vault_by_miner.assert_called_once_with(80)
        mock_repo.check_vault_meets_minimum.assert_called_once_with(mock_vault)

    @pytest.mark.asyncio
    async def test_is_miner_eligible_below_minimum(self):
        """Test eligibility when vault is below minimum balance."""
        mock_vault = MagicMock(spec=MinerVault)

        mock_repo = MagicMock(spec=VaultRepository)
        mock_repo.get_verified_vault_by_miner = AsyncMock(return_value=mock_vault)
        mock_repo.check_vault_meets_minimum = AsyncMock(return_value=False)

        service = VaultService(vault_repository=mock_repo)
        eligible = await service.is_miner_eligible_for_evaluation(
            miner_uid=80,
            require_verified=True,
            require_minimum_balance=True,
        )

        assert eligible is False

    @pytest.mark.asyncio
    async def test_filter_eligible_miners(self):
        """Test filtering miners by vault eligibility."""
        mock_repo = MagicMock(spec=VaultRepository)

        # Setup mock responses for different miners
        async def mock_get_verified(miner_uid):
            if miner_uid == 90:
                return MagicMock(spec=MinerVault)  # Has verified vault
            return None  # Others don't

        async def mock_check_min(vault):
            return True  # All vaults meet minimum

        mock_repo.get_verified_vault_by_miner = AsyncMock(side_effect=mock_get_verified)
        mock_repo.check_vault_meets_minimum = AsyncMock(side_effect=mock_check_min)

        service = VaultService(vault_repository=mock_repo)
        all_miners = [90, 91, 92, 93]
        eligible = await service.filter_eligible_miners(
            miner_uids=all_miners,
            require_verified=True,
            require_minimum_balance=True,
        )

        # Only miner 90 should be eligible
        assert eligible == [90]

    @pytest.mark.asyncio
    async def test_filter_eligible_miners_no_minimum_check(self):
        """Test filtering without minimum balance requirement."""
        mock_vault = MagicMock(spec=MinerVault)

        mock_repo = MagicMock(spec=VaultRepository)
        mock_repo.get_verified_vault_by_miner = AsyncMock(return_value=mock_vault)

        service = VaultService(vault_repository=mock_repo)
        eligible = await service.filter_eligible_miners(
            miner_uids=[100],
            require_verified=True,
            require_minimum_balance=False,  # Skip minimum check
        )

        assert eligible == [100]
        # check_vault_meets_minimum should not be called
        mock_repo.check_vault_meets_minimum.assert_not_called()


class TestVaultServiceOwnershipVerification:
    """Tests for vault ownership verification (mocked)."""

    @pytest.mark.asyncio
    async def test_verify_vault_ownership_success(self):
        """Test successful ownership verification."""
        service = VaultService()

        # Mock the web3 helper and contract
        with patch("validator.services.vault.AsyncWeb3Helper") as mock_web3_helper:
            mock_contract = MagicMock()
            mock_contract.functions.owner.return_value.call = AsyncMock(
                return_value="0x1234567890123456789012345678901234567890"
            )

            mock_web3_instance = MagicMock()
            mock_web3_instance.make_contract_by_name.return_value = mock_contract
            mock_web3_helper.make_web3.return_value = mock_web3_instance

            result = await service.verify_vault_ownership(
                vault_address="0xvault1234567890abcdef1234567890ab",
                expected_owner="0x1234567890123456789012345678901234567890",
                chain_id=8453,
            )

            assert result is True

    @pytest.mark.asyncio
    async def test_verify_vault_ownership_mismatch(self):
        """Test ownership verification with mismatched owner."""
        service = VaultService()

        with patch("validator.services.vault.AsyncWeb3Helper") as mock_web3_helper:
            mock_contract = MagicMock()
            mock_contract.functions.owner.return_value.call = AsyncMock(
                return_value="0xdifferent_owner_address_here_12345678"
            )

            mock_web3_instance = MagicMock()
            mock_web3_instance.make_contract_by_name.return_value = mock_contract
            mock_web3_helper.make_web3.return_value = mock_web3_instance

            result = await service.verify_vault_ownership(
                vault_address="0xvault1234567890abcdef1234567890ab",
                expected_owner="0x1234567890123456789012345678901234567890",
                chain_id=8453,
            )

            assert result is False

    @pytest.mark.asyncio
    async def test_verify_vault_ownership_no_owner_function(self):
        """Test ownership verification when contract has no owner() function."""
        service = VaultService()

        with patch("validator.services.vault.AsyncWeb3Helper") as mock_web3_helper:
            mock_contract = MagicMock()
            mock_contract.functions.owner.return_value.call = AsyncMock(
                side_effect=Exception("No owner function")
            )

            mock_web3_instance = MagicMock()
            mock_web3_instance.make_contract_by_name.return_value = mock_contract
            mock_web3_helper.make_web3.return_value = mock_web3_instance

            result = await service.verify_vault_ownership(
                vault_address="0xvault1234567890abcdef1234567890ab",
                expected_owner="0x1234567890123456789012345678901234567890",
                chain_id=8453,
            )

            assert result is False


class TestAssociatedMinerVerification:
    """Tests for associatedMiner() verification."""

    @pytest.mark.asyncio
    async def test_verify_associated_miner_success(self):
        """Test successful associatedMiner verification."""
        service = VaultService()

        # Create a bytes32 representation of the hotkey
        # 5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY is Alice's address
        # Its AccountId32 is d43593c715fdd31c61141abd04a99fd6822c8558854ccde39a5684e7a56da27d
        expected_hotkey = "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"
        expected_bytes32 = bytes.fromhex("d43593c715fdd31c61141abd04a99fd6822c8558854ccde39a5684e7a56da27d")

        with patch("validator.services.vault.AsyncWeb3Helper") as mock_web3_helper:
            mock_contract = MagicMock()
            mock_contract.functions.associatedMiner.return_value.call = AsyncMock(
                return_value=expected_bytes32
            )

            mock_web3_instance = MagicMock()
            mock_web3_instance.make_contract_by_name.return_value = mock_contract
            mock_web3_helper.make_web3.return_value = mock_web3_instance

            result = await service.verify_associated_miner(
                vault_address="0xvault1234567890abcdef1234567890ab",
                expected_miner_hotkey=expected_hotkey,
                chain_id=8453,
            )

            assert result is True

    @pytest.mark.asyncio
    async def test_verify_associated_miner_mismatch(self):
        """Test associatedMiner verification with mismatched miner."""
        service = VaultService()

        # Return a different miner's bytes32 (Bob's AccountId32)
        # 5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty decodes to a different AccountId32
        wrong_bytes32 = bytes.fromhex("8eaf04151687736326c9fea17e25fc5287613693c912909cb226aa4794f26a48")

        with patch("validator.services.vault.AsyncWeb3Helper") as mock_web3_helper:
            mock_contract = MagicMock()
            mock_contract.functions.associatedMiner.return_value.call = AsyncMock(
                return_value=wrong_bytes32
            )

            mock_web3_instance = MagicMock()
            mock_web3_instance.make_contract_by_name.return_value = mock_contract
            mock_web3_helper.make_web3.return_value = mock_web3_instance

            # Expecting Alice but contract returns Bob
            result = await service.verify_associated_miner(
                vault_address="0xvault1234567890abcdef1234567890ab",
                expected_miner_hotkey="5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
                chain_id=8453,
            )

            assert result is False

    @pytest.mark.asyncio
    async def test_verify_associated_miner_hex_format(self):
        """Test associatedMiner verification with hex-encoded hotkey."""
        service = VaultService()

        # Use a hex-encoded hotkey (64 chars = 32 bytes)
        hex_hotkey = "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
        expected_bytes32 = bytes.fromhex(hex_hotkey)

        with patch("validator.services.vault.AsyncWeb3Helper") as mock_web3_helper:
            mock_contract = MagicMock()
            mock_contract.functions.associatedMiner.return_value.call = AsyncMock(
                return_value=expected_bytes32
            )

            mock_web3_instance = MagicMock()
            mock_web3_instance.make_contract_by_name.return_value = mock_contract
            mock_web3_helper.make_web3.return_value = mock_web3_instance

            result = await service.verify_associated_miner(
                vault_address="0xvault1234567890abcdef1234567890ab",
                expected_miner_hotkey=hex_hotkey,
                chain_id=8453,
            )

            assert result is True

    @pytest.mark.asyncio
    async def test_verify_associated_miner_contract_error(self):
        """Test associatedMiner verification when contract call fails."""
        service = VaultService()

        with patch("validator.services.vault.AsyncWeb3Helper") as mock_web3_helper:
            mock_contract = MagicMock()
            mock_contract.functions.associatedMiner.return_value.call = AsyncMock(
                side_effect=Exception("Contract call failed")
            )

            mock_web3_instance = MagicMock()
            mock_web3_instance.make_contract_by_name.return_value = mock_contract
            mock_web3_helper.make_web3.return_value = mock_web3_instance

            result = await service.verify_associated_miner(
                vault_address="0xvault1234567890abcdef1234567890ab",
                expected_miner_hotkey="5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
                chain_id=8453,
            )

            assert result is False

    def test_normalize_hotkey_hex_format(self):
        """Test normalizing a hex hotkey to bytes32."""
        service = VaultService()

        # 64-char hex string (32 bytes)
        hex_hotkey = "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
        result = service._normalize_hotkey_to_bytes32(hex_hotkey)
        assert result == hex_hotkey

        # With 0x prefix
        result_with_prefix = service._normalize_hotkey_to_bytes32("0x" + hex_hotkey)
        assert result_with_prefix == hex_hotkey

    def test_normalize_hotkey_ss58_format(self):
        """Test normalizing an SS58 hotkey to bytes32."""
        service = VaultService()

        # 5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY is Alice's address
        # Its AccountId32 is d43593c715fdd31c61141abd04a99fd6822c8558854ccde39a5684e7a56da27d
        ss58_hotkey = "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"
        result = service._normalize_hotkey_to_bytes32(ss58_hotkey)

        # Should be decoded properly to AccountId32
        expected = "d43593c715fdd31c61141abd04a99fd6822c8558854ccde39a5684e7a56da27d"
        assert result == expected


class TestVaultPerformanceScore:
    """Tests for vault performance scoring."""

    @pytest.mark.asyncio
    async def test_get_vault_performance_score_no_history(self):
        """Test performance score with no historical data."""
        mock_vault = MagicMock(spec=MinerVault)

        mock_repo = MagicMock(spec=VaultRepository)
        mock_repo.get_latest_snapshot = AsyncMock(return_value=None)

        service = VaultService(vault_repository=mock_repo)
        score = await service.get_vault_performance_score(mock_vault)

        assert score == 0.0

    @pytest.mark.asyncio
    async def test_get_vault_performance_score_no_old_snapshot(self):
        """Test performance score when no old snapshot exists."""
        mock_vault = MagicMock(spec=MinerVault)

        mock_latest = MagicMock(spec=VaultSnapshot)
        mock_latest.total_value_usd = Decimal("1500.00")

        mock_repo = MagicMock(spec=VaultRepository)
        mock_repo.get_latest_snapshot = AsyncMock(return_value=mock_latest)

        with patch.object(VaultSnapshot, 'filter') as mock_filter:
            mock_filter.return_value.order_by.return_value.first = AsyncMock(return_value=None)

            service = VaultService(vault_repository=mock_repo)
            score = await service.get_vault_performance_score(mock_vault, lookback_hours=24)

            assert score == 0.0

    @pytest.mark.asyncio
    async def test_get_vault_performance_score_with_growth(self):
        """Test performance score with vault growth."""
        mock_vault = MagicMock(spec=MinerVault)

        mock_latest = MagicMock(spec=VaultSnapshot)
        mock_latest.total_value_usd = Decimal("1500.00")

        mock_old = MagicMock(spec=VaultSnapshot)
        mock_old.total_value_usd = Decimal("1000.00")

        mock_repo = MagicMock(spec=VaultRepository)
        mock_repo.get_latest_snapshot = AsyncMock(return_value=mock_latest)

        with patch.object(VaultSnapshot, 'filter') as mock_filter:
            mock_filter.return_value.order_by.return_value.first = AsyncMock(return_value=mock_old)

            service = VaultService(vault_repository=mock_repo)
            score = await service.get_vault_performance_score(mock_vault, lookback_hours=24)

            # 50% growth: (1500 - 1000) / 1000 = 0.5
            assert score == pytest.approx(0.5, rel=0.01)

    @pytest.mark.asyncio
    async def test_get_vault_performance_score_with_loss(self):
        """Test performance score with vault loss."""
        mock_vault = MagicMock(spec=MinerVault)

        mock_latest = MagicMock(spec=VaultSnapshot)
        mock_latest.total_value_usd = Decimal("800.00")

        mock_old = MagicMock(spec=VaultSnapshot)
        mock_old.total_value_usd = Decimal("1000.00")

        mock_repo = MagicMock(spec=VaultRepository)
        mock_repo.get_latest_snapshot = AsyncMock(return_value=mock_latest)

        with patch.object(VaultSnapshot, 'filter') as mock_filter:
            mock_filter.return_value.order_by.return_value.first = AsyncMock(return_value=mock_old)

            service = VaultService(vault_repository=mock_repo)
            score = await service.get_vault_performance_score(mock_vault, lookback_hours=24)

            # 20% loss: (800 - 1000) / 1000 = -0.2
            assert score == pytest.approx(-0.2, rel=0.01)


class TestRoundOrchestratorVaultIntegration:
    """Test vault integration with round orchestrator (mocked)."""

    @pytest.mark.asyncio
    async def test_vault_filtering_disabled(self):
        """Test that vault filtering can be disabled."""
        # This test verifies the feature flag works
        from validator.round_orchestrator import AsyncRoundOrchestrator
        from validator.repositories.job import JobRepository

        mock_job_repo = MagicMock(spec=JobRepository)
        mock_dendrite = MagicMock()
        mock_metagraph = MagicMock()
        mock_metagraph.S = [1.0, 1.0, 1.0]  # 3 active miners
        mock_metagraph.hotkeys = ["h1", "h2", "h3"]

        config = {
            "require_vault_for_evaluation": False,  # Disabled
            "rebalance_check_interval": 100,
        }

        orchestrator = AsyncRoundOrchestrator(
            job_repository=mock_job_repo,
            dendrite=mock_dendrite,
            metagraph=mock_metagraph,
            config=config,
        )

        # Verify the flag is set correctly
        assert orchestrator.require_vault_for_evaluation is False


class TestCryptoUtils:
    """Tests for SS58 encoding/decoding utilities."""

    def test_ss58_to_bytes32_alice(self):
        """Test decoding Alice's well-known SS58 address."""
        from validator.utils.crypto import ss58_to_bytes32

        # Alice's well-known address
        alice_ss58 = "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"
        result = ss58_to_bytes32(alice_ss58)

        # Known AccountId32 for Alice
        expected = bytes.fromhex("d43593c715fdd31c61141abd04a99fd6822c8558854ccde39a5684e7a56da27d")
        assert result == expected

    def test_ss58_to_bytes32_bob(self):
        """Test decoding Bob's well-known SS58 address."""
        from validator.utils.crypto import ss58_to_bytes32

        # Bob's well-known address
        bob_ss58 = "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"
        result = ss58_to_bytes32(bob_ss58)

        # Known AccountId32 for Bob
        expected = bytes.fromhex("8eaf04151687736326c9fea17e25fc5287613693c912909cb226aa4794f26a48")
        assert result == expected

    def test_ss58_to_bytes32_invalid_short(self):
        """Test decoding an invalid short SS58 address."""
        from validator.utils.crypto import ss58_to_bytes32

        with pytest.raises(ValueError, match="Invalid SS58 address length"):
            ss58_to_bytes32("abc")

    def test_ss58_to_bytes32_invalid_checksum(self):
        """Test decoding an SS58 address with invalid checksum."""
        from validator.utils.crypto import ss58_to_bytes32

        # Alter the last character to break the checksum
        invalid_ss58 = "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQZ"
        with pytest.raises(ValueError, match="Invalid SS58 checksum"):
            ss58_to_bytes32(invalid_ss58)

    def test_is_valid_ss58_valid(self):
        """Test is_valid_ss58 returns True for valid addresses."""
        from validator.utils.crypto import is_valid_ss58

        assert is_valid_ss58("5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY") is True
        assert is_valid_ss58("5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty") is True

    def test_is_valid_ss58_invalid(self):
        """Test is_valid_ss58 returns False for invalid addresses."""
        from validator.utils.crypto import is_valid_ss58

        assert is_valid_ss58("invalid") is False
        assert is_valid_ss58("abc") is False
        assert is_valid_ss58("0x1234567890") is False
        assert is_valid_ss58("") is False


class TestVaultRegistrationSynapse:
    """Tests for VaultRegistrationQuery synapse."""

    def test_vault_registration_query_defaults(self):
        """Test VaultRegistrationQuery default values."""
        from protocol.synapses import VaultRegistrationQuery

        synapse = VaultRegistrationQuery()
        assert synapse.has_vault is False
        assert synapse.vault_address is None
        assert synapse.chain_id == 8453

    def test_vault_registration_query_with_vault(self):
        """Test VaultRegistrationQuery with vault info."""
        from protocol.synapses import VaultRegistrationQuery

        synapse = VaultRegistrationQuery(
            has_vault=True,
            vault_address="0x1234567890abcdef1234567890abcdef12345678",
            chain_id=8453,
        )
        assert synapse.has_vault is True
        assert synapse.vault_address == "0x1234567890abcdef1234567890abcdef12345678"
        assert synapse.chain_id == 8453

    def test_vault_registration_query_deserialize(self):
        """Test VaultRegistrationQuery deserialization."""
        from protocol.synapses import VaultRegistrationQuery

        synapse = VaultRegistrationQuery(
            has_vault=True,
            vault_address="0xtest",
        )
        deserialized = synapse.deserialize()
        assert deserialized is synapse


class TestVaultRegistrationFlow:
    """Tests for vault registration flow in round orchestrator."""

    @pytest.mark.asyncio
    async def test_check_and_register_new_miners_no_unregistered(self):
        """Test check_and_register when all miners are registered."""
        from validator.round_orchestrator import AsyncRoundOrchestrator
        from validator.repositories.job import JobRepository

        mock_job_repo = MagicMock(spec=JobRepository)
        mock_dendrite = MagicMock()
        mock_metagraph = MagicMock()
        mock_metagraph.S = [1.0, 1.0]  # 2 active miners
        mock_metagraph.hotkeys = ["hotkey1", "hotkey2"]

        config = {"require_vault_for_evaluation": False}

        orchestrator = AsyncRoundOrchestrator(
            job_repository=mock_job_repo,
            dendrite=mock_dendrite,
            metagraph=mock_metagraph,
            config=config,
        )

        # Mock vault repository to return all miners as registered
        orchestrator.vault_service.vault_repository.get_registered_miner_uids = AsyncMock(
            return_value=[0, 1]
        )

        # Should not query any miners
        orchestrator._query_and_register_miner_vault = AsyncMock()

        await orchestrator._check_and_register_new_miners_in_db()

        orchestrator._query_and_register_miner_vault.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_and_register_new_miners_with_unregistered(self):
        """Test check_and_register when some miners are unregistered."""
        from validator.round_orchestrator import AsyncRoundOrchestrator
        from validator.repositories.job import JobRepository

        mock_job_repo = MagicMock(spec=JobRepository)
        mock_dendrite = MagicMock()
        mock_metagraph = MagicMock()
        mock_metagraph.S = [1.0, 1.0, 1.0]  # 3 active miners
        mock_metagraph.hotkeys = ["hotkey1", "hotkey2", "hotkey3"]

        config = {"require_vault_for_evaluation": False}

        orchestrator = AsyncRoundOrchestrator(
            job_repository=mock_job_repo,
            dendrite=mock_dendrite,
            metagraph=mock_metagraph,
            config=config,
        )

        # Mock vault repository to return only miner 0 as registered
        orchestrator.vault_service.vault_repository.get_registered_miner_uids = AsyncMock(
            return_value=[0]
        )

        # Mock the query method
        orchestrator._query_and_register_miner_vault = AsyncMock()

        await orchestrator._check_and_register_new_miners_in_db()

        # Should query miners 1 and 2 (unregistered)
        assert orchestrator._query_and_register_miner_vault.call_count == 2
        called_uids = [
            call.args[0] for call in orchestrator._query_and_register_miner_vault.call_args_list
        ]
        assert set(called_uids) == {1, 2}

    @pytest.mark.asyncio
    async def test_query_and_register_miner_vault_success(self):
        """Test successful vault registration from miner query."""
        from validator.round_orchestrator import AsyncRoundOrchestrator
        from validator.repositories.job import JobRepository
        from protocol.synapses import VaultRegistrationQuery

        mock_job_repo = MagicMock(spec=JobRepository)
        mock_metagraph = MagicMock()
        mock_metagraph.hotkeys = ["hotkey1"]
        mock_metagraph.axons = [MagicMock()]

        # Create mock response synapse
        mock_response = VaultRegistrationQuery(
            has_vault=True,
            vault_address="0xmyvault123",
            chain_id=8453,
        )

        mock_dendrite = AsyncMock(return_value=[mock_response])

        config = {"require_vault_for_evaluation": False}

        orchestrator = AsyncRoundOrchestrator(
            job_repository=mock_job_repo,
            dendrite=mock_dendrite,
            metagraph=mock_metagraph,
            config=config,
        )

        # Mock register_miner_vault
        mock_vault = MagicMock()
        mock_vault.is_verified = True
        orchestrator.vault_service.register_miner_vault = AsyncMock(return_value=mock_vault)

        await orchestrator._query_and_register_miner_vault(0)

        # Verify register was called with correct args
        orchestrator.vault_service.register_miner_vault.assert_called_once_with(
            miner_uid=0,
            miner_hotkey="hotkey1",
            vault_address="0xmyvault123",
            chain_id=8453,
            auto_verify=True,
        )

    @pytest.mark.asyncio
    async def test_query_and_register_miner_vault_no_vault(self):
        """Test when miner has no vault configured."""
        from validator.round_orchestrator import AsyncRoundOrchestrator
        from validator.repositories.job import JobRepository
        from protocol.synapses import VaultRegistrationQuery

        mock_job_repo = MagicMock(spec=JobRepository)
        mock_metagraph = MagicMock()
        mock_metagraph.hotkeys = ["hotkey1"]
        mock_metagraph.axons = [MagicMock()]

        # Create mock response with no vault
        mock_response = VaultRegistrationQuery(has_vault=False)

        mock_dendrite = AsyncMock(return_value=[mock_response])

        config = {"require_vault_for_evaluation": False}

        orchestrator = AsyncRoundOrchestrator(
            job_repository=mock_job_repo,
            dendrite=mock_dendrite,
            metagraph=mock_metagraph,
            config=config,
        )

        # Mock register_miner_vault
        orchestrator.vault_service.register_miner_vault = AsyncMock()

        await orchestrator._query_and_register_miner_vault(0)

        # Should NOT call register since miner has no vault
        orchestrator.vault_service.register_miner_vault.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_registered_miner_uids(self):
        """Test getting list of registered miner UIDs."""
        mock_vaults = [
            MagicMock(miner_uid=0),
            MagicMock(miner_uid=5),
            MagicMock(miner_uid=10),
        ]

        with patch.object(MinerVault, 'filter') as mock_filter:
            mock_filter.return_value.all = AsyncMock(return_value=mock_vaults)

            repo = VaultRepository()
            uids = await repo.get_registered_miner_uids()

            assert uids == [0, 5, 10]

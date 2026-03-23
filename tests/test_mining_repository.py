"""
Tests for MiningRepository — vault mining tracking persistence.

Unit tests mock the ORM models. Integration tests write to the `validator` schema.
"""

import unittest
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from tortoise import Tortoise

from validator.models.miner_tracking import VaultMiningCycle, VaultMiningExecution
from validator.utils.env import (
    JOBS_POSTGRES_HOST,
    JOBS_POSTGRES_PORT,
    JOBS_POSTGRES_DB,
    JOBS_POSTGRES_USER,
    JOBS_POSTGRES_PASSWORD,
)
from miner.utils.mining_repository import MiningRepository


# -- Helpers ------------------------------------------------------------------

TEST_MINER_UID = 99
TEST_MINER_HOTKEY = "5TestHotkey_mining_repo_test_0000000000000000000000"
TEST_VAULT = "0x1234567890abcdef12345678901234567890abcd"
TEST_POOL = "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd"
TEST_WIDTH_FACTOR = 3.0


def _make_position(tick_lower: int, tick_upper: int):
    pos = MagicMock()
    pos.tick_lower = tick_lower
    pos.tick_upper = tick_upper
    return pos


def _make_inventory(amount0, amount1):
    inv = MagicMock()
    inv.amount0 = amount0
    inv.amount1 = amount1
    return inv


def _repo():
    return MiningRepository(TEST_MINER_UID, TEST_MINER_HOTKEY, TEST_WIDTH_FACTOR)


# -- Unit Tests: save_mining_snapshot -----------------------------------------


class TestSaveMiningSnapshot:

    @pytest.mark.asyncio
    async def test_save_snapshot_with_rebalance(self):
        with patch.object(
            VaultMiningCycle, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = MagicMock(spec=VaultMiningCycle)

            pos = _make_position(tick_lower=-100, tick_upper=100)
            inv = _make_inventory(amount0=5000, amount1=3000)
            new_pos = _make_position(tick_lower=-200, tick_upper=200)

            result = await _repo().save_mining_snapshot(
                vault_address=TEST_VAULT,
                pool_address=TEST_POOL,
                current_tick=50,
                current_price=79228162514264337593543950336,
                tick_spacing=10,
                current_positions=[pos],
                inventory=inv,
                volatility=0.005,
                computed_width=150.0,
                rebalance_reason="price_near_edge",
                new_position=new_pos,
                execution_triggered=True,
            )

            assert result is not None
            mock_create.assert_called_once()
            kwargs = mock_create.call_args.kwargs

            assert kwargs["miner_uid"] == TEST_MINER_UID
            assert kwargs["miner_hotkey"] == TEST_MINER_HOTKEY
            assert kwargs["should_rebalance"] is True
            assert kwargs["num_positions"] == 1
            assert kwargs["position_tick_lower"] == -100
            assert kwargs["position_tick_upper"] == 100
            assert kwargs["inventory_amount0"] == Decimal("5000")
            assert kwargs["inventory_amount1"] == Decimal("3000")
            assert kwargs["new_tick_lower"] == -200
            assert kwargs["new_tick_upper"] == 200
            assert kwargs["width_factor"] == TEST_WIDTH_FACTOR
            assert kwargs["current_price"] == Decimal("79228162514264337593543950336")
            assert kwargs["execution_triggered"] is True

    @pytest.mark.asyncio
    async def test_save_snapshot_no_rebalance(self):
        with patch.object(
            VaultMiningCycle, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = MagicMock(spec=VaultMiningCycle)

            inv = _make_inventory(amount0=1000, amount1=2000)

            await _repo().save_mining_snapshot(
                vault_address=TEST_VAULT,
                pool_address=TEST_POOL,
                current_tick=0,
                current_price=100000,
                tick_spacing=10,
                current_positions=[],
                inventory=inv,
                volatility=0.0,
                computed_width=100.0,
                rebalance_reason=None,
                new_position=None,
                execution_triggered=False,
            )

            kwargs = mock_create.call_args.kwargs

            assert kwargs["should_rebalance"] is False
            assert kwargs["num_positions"] == 0
            assert kwargs["position_tick_lower"] is None
            assert kwargs["position_tick_upper"] is None
            assert kwargs["new_tick_lower"] is None
            assert kwargs["new_tick_upper"] is None
            assert kwargs["execution_triggered"] is False

    @pytest.mark.asyncio
    async def test_save_snapshot_db_failure_returns_none(self):
        with patch.object(
            VaultMiningCycle, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.side_effect = Exception("DB connection lost")

            inv = _make_inventory(amount0=0, amount1=0)

            result = await _repo().save_mining_snapshot(
                vault_address=TEST_VAULT,
                pool_address=TEST_POOL,
                current_tick=0,
                current_price=100000,
                tick_spacing=10,
                current_positions=[],
                inventory=inv,
                volatility=0.0,
                computed_width=100.0,
                rebalance_reason=None,
                new_position=None,
                execution_triggered=False,
            )

            assert result is None


# -- Unit Tests: create_mining_execution --------------------------------------


class TestCreateMiningExecution:

    @pytest.mark.asyncio
    async def test_create_execution_success(self):
        with patch.object(
            VaultMiningExecution, "create", new_callable=AsyncMock
        ) as mock_create:
            snapshot = MagicMock(spec=VaultMiningCycle)

            await _repo().create_mining_execution(
                snapshot=snapshot,
                vault_address=TEST_VAULT,
                pool_address=TEST_POOL,
                round_id="miner-vault-0x12345678-1234567890",
                positions_data=[{"tick_lower": -100, "tick_upper": 100}],
                executor_status_code=200,
                tx_hash="0xabc123",
                error=None,
            )

            mock_create.assert_called_once()
            kwargs = mock_create.call_args.kwargs

            assert kwargs["miner_uid"] == TEST_MINER_UID
            assert kwargs["miner_hotkey"] == TEST_MINER_HOTKEY
            assert kwargs["snapshot"] is snapshot
            assert kwargs["tx_hash"] == "0xabc123"
            assert kwargs["error"] is None

    @pytest.mark.asyncio
    async def test_create_execution_null_snapshot(self):
        with patch.object(
            VaultMiningExecution, "create", new_callable=AsyncMock
        ) as mock_create:
            await _repo().create_mining_execution(
                snapshot=None,
                vault_address=TEST_VAULT,
                pool_address=TEST_POOL,
                round_id="miner-vault-0x12345678-9999999999",
                positions_data=[],
                executor_status_code=500,
                tx_hash=None,
                error="server error",
            )

            kwargs = mock_create.call_args.kwargs
            assert kwargs["snapshot"] is None

    @pytest.mark.asyncio
    async def test_create_execution_db_failure_no_raise(self):
        with patch.object(
            VaultMiningExecution, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.side_effect = Exception("DB timeout")

            # Should not raise
            await _repo().create_mining_execution(
                snapshot=None,
                vault_address=TEST_VAULT,
                pool_address=TEST_POOL,
                round_id="miner-vault-0x12345678-0000000000",
                positions_data=[],
                executor_status_code=None,
                tx_hash=None,
                error="connection failed",
            )


# -- Integration Tests (real DB, `validator` schema) --------------------------


@pytest.mark.integration
class TestMiningRepositoryIntegration(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        await Tortoise.init(
            config={
                "connections": {
                    "default": {
                        "engine": "tortoise.backends.asyncpg",
                        "credentials": {
                            "host": JOBS_POSTGRES_HOST,
                            "port": JOBS_POSTGRES_PORT,
                            "user": JOBS_POSTGRES_USER,
                            "password": JOBS_POSTGRES_PASSWORD,
                            "database": JOBS_POSTGRES_DB,
                            "schema": "validator",
                        },
                    }
                },
                "apps": {
                    "models": {
                        "models": ["validator.models.miner_tracking"],
                        "default_connection": "default",
                    }
                },
            }
        )
        await Tortoise.generate_schemas(safe=True)
        self.repo = MiningRepository(
            TEST_MINER_UID, TEST_MINER_HOTKEY, TEST_WIDTH_FACTOR
        )

    async def asyncTearDown(self):
        # Clean up test rows
        try:
            await VaultMiningExecution.filter(miner_hotkey=TEST_MINER_HOTKEY).delete()
            await VaultMiningCycle.filter(miner_hotkey=TEST_MINER_HOTKEY).delete()
        except Exception:
            pass

        if Tortoise._inited:
            await Tortoise.close_connections()

    async def test_save_and_read_snapshot(self):
        pos = _make_position(tick_lower=-50, tick_upper=50)
        inv = _make_inventory(amount0=10000, amount1=20000)

        snapshot = await self.repo.save_mining_snapshot(
            vault_address=TEST_VAULT,
            pool_address=TEST_POOL,
            current_tick=10,
            current_price=79228162514264337593543950336,
            tick_spacing=1,
            current_positions=[pos],
            inventory=inv,
            volatility=0.003,
            computed_width=120.0,
            rebalance_reason="no_positions",
            new_position=_make_position(-200, 200),
            execution_triggered=False,
        )

        assert snapshot is not None

        row = await VaultMiningCycle.get(id=snapshot.id)
        assert row.miner_uid == TEST_MINER_UID
        assert row.miner_hotkey == TEST_MINER_HOTKEY
        assert row.vault_address == TEST_VAULT
        assert row.current_tick == 10
        assert row.num_positions == 1
        assert row.position_tick_lower == -50
        assert row.position_tick_upper == 50
        assert row.inventory_amount0 == Decimal("10000")
        assert row.inventory_amount1 == Decimal("20000")
        assert row.should_rebalance is True
        assert row.rebalance_reason == "no_positions"
        assert row.new_tick_lower == -200
        assert row.new_tick_upper == 200
        assert row.width_factor == TEST_WIDTH_FACTOR

    async def test_create_and_read_execution(self):
        inv = _make_inventory(amount0=100, amount1=200)
        snapshot = await self.repo.save_mining_snapshot(
            vault_address=TEST_VAULT,
            pool_address=TEST_POOL,
            current_tick=0,
            current_price=100000,
            tick_spacing=1,
            current_positions=[],
            inventory=inv,
            volatility=0.0,
            computed_width=10.0,
            rebalance_reason="no_positions",
            new_position=None,
            execution_triggered=True,
        )

        round_id = f"test-round-{snapshot.id}"
        await self.repo.create_mining_execution(
            snapshot=snapshot,
            vault_address=TEST_VAULT,
            pool_address=TEST_POOL,
            round_id=round_id,
            positions_data=[{"tick_lower": -10, "tick_upper": 10}],
            executor_status_code=200,
            tx_hash="0xdeadbeef",
            error=None,
        )

        row = await VaultMiningExecution.get(round_id=round_id)
        assert row.miner_uid == TEST_MINER_UID
        assert row.miner_hotkey == TEST_MINER_HOTKEY
        assert row.snapshot_id == snapshot.id
        assert row.tx_hash == "0xdeadbeef"
        assert row.executor_status_code == 200
        assert row.error is None

    async def test_create_execution_without_snapshot(self):
        round_id = "test-round-no-snapshot"
        await self.repo.create_mining_execution(
            snapshot=None,
            vault_address=TEST_VAULT,
            pool_address=TEST_POOL,
            round_id=round_id,
            positions_data=[],
            executor_status_code=None,
            tx_hash=None,
            error="test error",
        )

        row = await VaultMiningExecution.get(round_id=round_id)
        assert row.snapshot_id is None
        assert row.error == "test error"

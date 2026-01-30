"""
Project-wide pytest fixtures.

Use these across test modules. Helpers live in tests.common.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

import pytest

# Ensure project root on path before any local imports
from tests.common import (
    build_mock_metagraph_for_miner,
    create_test_wallets,
    ensure_project_root,
    get_test_wallets_path,
    start_miner_process,
)

ensure_project_root()

logger = logging.getLogger(__name__)

# Defaults for full-flow–style tests
MINER_PORT = 8092
MINER_IP = "127.0.0.1"


@pytest.fixture(scope="session")
def test_wallets_path() -> str:
    """Wallet directory for tests. Same path used by miner subprocess in full-flow."""
    path = get_test_wallets_path()
    os.makedirs(path, exist_ok=True)
    return path


@pytest.fixture
def validator_wallet(test_wallets_path: str):
    """Create validator test wallet. Reused across tests using same path."""
    val, _ = create_test_wallets(test_wallets_path)
    return val


@pytest.fixture
def miner_wallet(test_wallets_path: str):
    """Create miner test wallet. Reused across tests using same path."""
    _, miner = create_test_wallets(test_wallets_path)
    return miner


@pytest.fixture
def mock_metagraph_with_miner(miner_wallet):
    """Mock Metagraph with a single miner (full-flow style). Uses MINER_IP, MINER_PORT."""
    return build_mock_metagraph_for_miner(miner_wallet, ip=MINER_IP, port=MINER_PORT)


@pytest.fixture
async def miner_process(miner_wallet, test_wallets_path: str):
    """
    Start miner subprocess; yield for test; terminate on teardown.
    Use in tests that need a live miner (e.g. full flow).
    """
    proc = await start_miner_process(
        wallet_path=test_wallets_path,
        port=MINER_PORT,
        miner_name="test_miner",
        miner_hotkey="test_hotkey",
        wait_seconds=5.0,
    )
    try:
        yield proc
    finally:
        if proc.poll() is None:
            logger.info("Stopping miner subprocess...")
            proc.terminate()
            proc.wait(timeout=10)

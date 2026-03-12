"""
Shared test helpers and utilities for project-wide use.

Use these from conftest.py fixtures, unittest setUp, or individual tests.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Tuple

# Ensure project root is on path when tests run
def _ensure_project_root() -> Path:
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


def ensure_project_root() -> Path:
    """Add project root to sys.path. Idempotent. Returns root Path."""
    return _ensure_project_root()


def get_test_wallets_path() -> str:
    """Default wallet directory for tests (under project root)."""
    root = ensure_project_root()
    return str(root / "wallets")


def create_test_wallets(
    wallet_path: str | None = None,
    *,
    validator_name: str = "test_validator",
    validator_hotkey: str = "test_validator_hotkey",
    miner_name: str = "test_miner",
    miner_hotkey: str = "test_hotkey",
) -> Tuple[object, object]:
    """
    Create validator and miner test wallets.

    Both use the same wallet_path. Wallets are created if non-existent.
    Returns (validator_wallet, miner_wallet).
    """
    import bittensor as bt

    path = wallet_path or get_test_wallets_path()
    os.makedirs(path, exist_ok=True)

    val = bt.Wallet(name=validator_name, hotkey=validator_hotkey, path=path)
    val.create_if_non_existent(coldkey_use_password=False, hotkey_use_password=False)

    miner = bt.Wallet(name=miner_name, hotkey=miner_hotkey, path=path)
    miner.create_if_non_existent(coldkey_use_password=False, hotkey_use_password=False)

    return (val, miner)


def build_mock_metagraph_for_miner(
    miner_wallet: object,
    ip: str = "127.0.0.1",
    port: int = 8092,
) -> object:
    """Build a MagicMock Metagraph with a single miner axon (for full-flow style tests)."""
    from unittest.mock import MagicMock
    import bittensor as bt

    m = MagicMock(spec=bt.Metagraph)
    m.S = [1.0]
    m.uids = [0]
    m.hotkeys = [miner_wallet.hotkey.ss58_address]
    m.axons = [
        bt.AxonInfo(
            version=1,
            ip=ip,
            port=port,
            ip_type=4,
            hotkey=miner_wallet.hotkey.ss58_address,
            coldkey=miner_wallet.coldkeypub.ss58_address,
        )
    ]
    return m


async def start_miner_process(
    wallet_path: str,
    port: int = 8092,
    miner_name: str = "test_miner",
    miner_hotkey: str = "test_hotkey",
    wait_seconds: float = 5.0,
) -> subprocess.Popen:
    """Start miner subprocess. Wait wait_seconds for startup. Caller must terminate."""
    root = ensure_project_root()
    env = os.environ.copy()
    env["AXON_PORT"] = str(port)
    cmd = [
        sys.executable, "-u", "-m", "miner.miner",
        "--wallet.name", miner_name,
        "--wallet.hotkey", miner_hotkey,
        "--wallet.path", wallet_path,
        "--axon.port", str(port),
        "--subtensor.network", "test",
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=str(root),
    )
    await asyncio.sleep(wait_seconds)
    return proc

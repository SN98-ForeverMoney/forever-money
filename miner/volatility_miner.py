"""
Volatility-Aware Single-Position Miner implementation for SN98 ForeverMoney using Bittensor axon.

This module implements a minimal LP strategy where a single liquidity position
recenters dynamically based on recent market volatility.

Key Features:
- Single LP position
- Rebalance triggered when price nears edges of current position
- Position width scales with recent price volatility
- Vault mining reads on-chain state directly (no validator dependency)

Usage:
    python -m miner.volatility_miner --wallet.name <wallet_name> --wallet.hotkey <hotkey_name> --width_factor 3 --volatility_window 10
"""

import logging
import argparse
import time
import bittensor as bt
import os
import sys
import math
import asyncio
import httpx
import json

from collections import deque
from typing import Optional, Tuple, Any, List

from protocol import Position
from protocol.synapses import RebalanceQuery, VaultRegistrationQuery
from validator.utils.env import (
    MINER_VERSION,
    NETUID,
    SUBTENSOR_NETWORK,
    get_env_variable,
)


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


from protocol.synapses import RebalanceQuery
from protocol.models import Position
from validator.utils.env import MINER_VERSION, NETUID, SUBTENSOR_NETWORK, BT_WALLET_PATH
from validator.utils.math import UniswapV3Math
from validator.utils.web3 import AsyncWeb3Helper, ZERO_ADDRESS
from validator.services.liqmanager import SnLiqManagerService
from web3 import Web3
from validator.models.job import init_db, close_db
from validator.utils.env import (
    JOBS_POSTGRES_USER,
    JOBS_POSTGRES_PASSWORD,
    JOBS_POSTGRES_HOST,
    JOBS_POSTGRES_PORT,
    JOBS_POSTGRES_DB,
    JOBS_POSTGRES_SCHEMA,
)
from miner.utils.mining_repository import MiningRepository

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def discover_vault_pool(vault_address: str, chain_id: int) -> Optional[str]:
    """
    Discover the pool address for a vault by querying on-chain state.

    Checks common Base tokens to find which one is registered as an AK,
    then reads the pool address from the position manager.

    Returns the pool address or None if no pool is registered.
    """
    w3 = AsyncWeb3Helper.make_web3(chain_id)
    lm = w3.make_contract_by_name("LiquidityManager", vault_address)

    # Known tokens on Base to check
    tokens_to_check = [
        ("WETH", "0x4200000000000000000000000000000000000006"),
        ("USDC", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"),
        ("cbBTC", "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf"),
        ("BID", "0xa1832f7F4e534aE557f9B5AB76dE54B1873e498B"),
        ("xTAO", "0xb99FBE68c8A0cC14bE8c1AF73DD4DfEA8a76aDD7"),
        ("USDbC", "0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA"),
        ("DAI", "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb"),
    ]

    for name, token in tokens_to_check:
        try:
            pm_addr = await lm.functions.akAddressToPositionManager(
                Web3.to_checksum_address(token)
            ).call()
            if pm_addr == ZERO_ADDRESS:
                continue

            pm = w3.make_contract_by_name("AeroCLPositionManager", pm_addr)
            pool_bytes = await pm.functions.pool().call()
            # Pool address is the first 20 bytes of the bytes32
            if isinstance(pool_bytes, bytes):
                pool_address = "0x" + pool_bytes[:20].hex()
            else:
                pool_address = "0x" + str(pool_bytes)[:40]

            # Verify it's a valid pool by calling slot0
            pool_contract = w3.make_contract_by_name("ICLPool", pool_address)
            await pool_contract.functions.slot0().call()

            logger.info(
                f"Discovered pool {pool_address} for vault {vault_address} (AK={name})"
            )
            return pool_address
        except Exception:
            continue

    return None


class SN98Miner:
    """
    SN98 ForeverMoney Miner using rebalance-only protocol.

    Serves one endpoint:
    - RebalanceQuery: Dynamic rebalancing decisions during backtesting
    """

    def __init__(
        self,
        wallet: Any,  # bt.Wallet
        subtensor: Any,  # bt.Subtensor
        config: Any,  # bt.Config
    ):
        """
        Initialize miner.

        Args:
            wallet: Bittensor wallet for authentication
            subtensor: Bittensor subtensor connection
            config: Configuration object
        """
        self.wallet = wallet
        self.subtensor = subtensor
        self.config = config

        # Vault configuration (optional - for miner-owned vaults)
        raw_vault_addresses = get_env_variable("MINER_VAULT_ADDRESSES", str, "[]")
        self.vault_addresses = json.loads(raw_vault_addresses)
        self.num_vault_addresses = len(self.vault_addresses)
        self.vault_chain_id = get_env_variable("MINER_VAULT_CHAIN_ID", int, 8453)
        self.vault_executor_interval = get_env_variable(
            "MINER_VAULT_EXECUTOR_INTERVAL", int, 900
        )

        # Executor bot configuration
        self.executor_bot_url = get_env_variable("EXECUTOR_BOT_URL", str, None)
        self.executor_bot_api_key = get_env_variable("EXECUTOR_BOT_API_KEY", str, None)

        # On-chain services per vault (populated during init_vault_services)
        self.vault_liq_services: dict[str, SnLiqManagerService] = {}

        # Volatility Miner Config
        self.width_factor = config.width_factor
        self.volatility_window = config.volatility_window
        self.recent_prices = {
            vault: deque(maxlen=self.volatility_window)
            for vault in self.vault_addresses
        }
        logger.info(f"Width factor: {self.width_factor}")
        logger.info(f"Volatility window: {self.volatility_window}")

        # Miner identity for DB tracking
        self.miner_hotkey = wallet.hotkey.ss58_address
        self.miner_uid: Optional[int] = None
        try:
            metagraph = subtensor.metagraph(config.netuid)
            if self.miner_hotkey in metagraph.hotkeys:
                self.miner_uid = metagraph.hotkeys.index(self.miner_hotkey)
        except Exception as e:
            logger.warning(f"Could not resolve miner UID from metagraph: {e}")

        self.mining_repo = MiningRepository(self.miner_uid, self.miner_hotkey, self.width_factor)

        logger.info(f"Starting SN98 Miner v{MINER_VERSION}")
        logger.info(f"Wallet: {self.miner_hotkey} (uid={self.miner_uid})")
        if self.vault_addresses:
            logger.info(f"Vaults: {self.vault_addresses} (chain {self.vault_chain_id})")

        # Create and configure axon
        self.axon = bt.Axon(wallet=wallet, config=config)

        # Attach RebalanceQuery handler
        self.axon.attach(
            forward_fn=self.rebalance_query_handler,
            blacklist_fn=self.blacklist_rebalance_query,
            priority_fn=self.priority_rebalance_query,
        )

        # Attach VaultRegistrationQuery handler
        self.axon.attach(
            forward_fn=self.vault_registration_handler,
            blacklist_fn=self.blacklist_vault_registration,
            priority_fn=self.priority_vault_registration,
        )

        logger.info(f"Axon created on port {self.axon.port}")
        logger.info(f"Serving RebalanceQuery and VaultRegistrationQuery endpoints")

    async def init_vault_services(self):
        """
        Discover pool addresses for each vault and initialize SnLiqManagerService instances.
        Vaults without a registered pool are skipped.
        """
        for vault_address in self.vault_addresses:
            pool_address = await discover_vault_pool(vault_address, self.vault_chain_id)
            if pool_address is None:
                logger.warning(
                    f"Vault {vault_address} has no registered pool — skipping vault mining"
                )
                continue

            self.vault_liq_services[vault_address] = SnLiqManagerService(
                chain_id=self.vault_chain_id,
                liquidity_manager_address=vault_address,
                pool_address=pool_address,
            )
            logger.info(
                f"Initialized on-chain service for vault {vault_address} -> pool {pool_address}"
            )

    async def rebalance_query_handler(self, synapse: RebalanceQuery) -> RebalanceQuery:
        """
        Handle RebalanceQuery synapse from validators.
        """

        logger.info(
            f"Received RebalanceQuery: block={synapse.block_number}, rebalances={synapse.rebalances_so_far}"
        )

        # Check if we should accept
        should_accept, refusal_reason = self._should_accept_job(synapse)
        if not should_accept:
            synapse.accepted = False
            synapse.refusal_reason = refusal_reason
            return synapse

        synapse.accepted = True

        current_tick = UniswapV3Math.get_tick_from_sqrt_price_x96(synapse.current_price)
        for vault in self.vault_addresses:
            self.recent_prices[vault].append(current_tick)

        should_rebalance = False
        if not synapse.current_positions or synapse.rebalances_so_far == 0:
            should_rebalance = True
            logger.info(
                f"Rebalancing: positions={len(synapse.current_positions or [])}, "
                f"rebalances_so_far={synapse.rebalances_so_far}"
            )
        else:
            pos = synapse.current_positions[0]
            tick_width = pos.tick_upper - pos.tick_lower
            buffer = tick_width * 0.2

            if (
                current_tick < pos.tick_lower + buffer
                or current_tick > pos.tick_upper - buffer
            ):
                should_rebalance = True
                logger.info(
                    f"Price (tick {current_tick}) near edge of [{pos.tick_lower}, {pos.tick_upper}]. Rebalancing."
                )

        if should_rebalance:
            tick_spacing = synapse.tick_spacing

            new_pos = self.compute_positions(
                vault_address=self.vault_addresses[0] if self.vault_addresses else "eval",
                current_tick=current_tick,
                tick_spacing=tick_spacing,
                inventory=synapse.inventory_remaining,
            )

            synapse.desired_positions = [new_pos]
            logger.info(f"Proposing new position: [{new_pos.tick_lower}, {new_pos.tick_upper}]")
        else:
            synapse.desired_positions = list(synapse.current_positions)
            logger.info("Keeping current positions.")

        return synapse

    def compute_positions(
        self, vault_address: str, current_tick: int, tick_spacing: int, inventory: dict
    ) -> Position:
        self.recent_prices.setdefault(vault_address, deque(maxlen=self.volatility_window))
        self.recent_prices[vault_address].append(current_tick)

        volatility = self.compute_volatility(self.recent_prices[vault_address])
        logger.info(
            f"Calculated volatility factor as {volatility} using recent prices: {list(self.recent_prices[vault_address])}"
        )
        min_width = tick_spacing * 10
        width = max(volatility * self.width_factor, min_width)

        amount0 = int(inventory["amount0"]) if inventory["amount0"] else 0
        amount1 = int(inventory["amount1"]) if inventory["amount1"] else 0

        # Dust threshold: if one side is negligible, place the range
        # out-of-range on that side so V3 only requires the non-dust token.
        # - token0 is dust → place range ABOVE current tick (only token1 needed)
        # - token1 is dust → place range BELOW current tick (only token0 needed)
        DUST_THRESHOLD = 10_000  # below this is dust

        center_tick = (current_tick // tick_spacing) * tick_spacing

        if amount0 <= DUST_THRESHOLD and amount1 > DUST_THRESHOLD:
            # Only token1 available → range above current tick
            lower_tick = center_tick + tick_spacing
            upper_tick = int(lower_tick + 2 * width) // tick_spacing * tick_spacing
            logger.info(
                f"compute_positions: token0 is dust ({amount0}), placing range above tick. "
                f"range=[{lower_tick}, {upper_tick}]"
            )
        elif amount1 <= DUST_THRESHOLD and amount0 > DUST_THRESHOLD:
            # Only token0 available → range below current tick
            upper_tick = center_tick - tick_spacing
            lower_tick = int(upper_tick - 2 * width) // tick_spacing * tick_spacing
            logger.info(
                f"compute_positions: token1 is dust ({amount1}), placing range below tick. "
                f"range=[{lower_tick}, {upper_tick}]"
            )
        else:
            # Both tokens available — center on current tick
            lower_tick = int(center_tick - width) // tick_spacing * tick_spacing
            upper_tick = int(center_tick + width) // tick_spacing * tick_spacing
            logger.info(
                f"compute_positions: both tokens available. tick={current_tick} "
                f"amount0={amount0} amount1={amount1} range=[{lower_tick}, {upper_tick}]"
            )

        new_pos = Position(
            tick_lower=lower_tick,
            tick_upper=upper_tick,
            allocation0=inventory["amount0"],
            allocation1=inventory["amount1"],
        )

        return new_pos

    def compute_volatility(self, recent_prices: List[int]) -> float:
        if len(recent_prices) < self.volatility_window:
            return 0.0

        price_changes = [
            (recent_prices[i] - recent_prices[i - 1]) / recent_prices[i - 1]
            for i in range(1, len(recent_prices))
        ]
        mean = sum(price_changes) / len(price_changes)
        variance = sum((x - mean) ** 2 for x in price_changes) / len(price_changes)
        return math.sqrt(variance)

    def _should_accept_job(self, synapse: RebalanceQuery) -> Tuple[bool, Optional[str]]:
        return True, None

    def blacklist_rebalance_query(self, synapse: RebalanceQuery) -> Tuple[bool, str]:
        return False, ""

    def priority_rebalance_query(self, synapse: RebalanceQuery) -> float:
        return 0.0

    async def vault_registration_handler(
        self, synapse: VaultRegistrationQuery
    ) -> VaultRegistrationQuery:
        logger.info("Received VaultRegistrationQuery")

        if self.vault_addresses:
            synapse.has_vault = True
            synapse.vault_address = self.vault_addresses[0]
            synapse.chain_id = self.vault_chain_id
            logger.info(f"Responding with vault: {self.vault_addresses[0]}")
        else:
            synapse.has_vault = False
            logger.info("No vault configured, responding with has_vault=False")

        return synapse

    def blacklist_vault_registration(
        self, synapse: VaultRegistrationQuery
    ) -> Tuple[bool, str]:
        return False, ""

    def priority_vault_registration(self, synapse: VaultRegistrationQuery) -> float:
        return 0.0

    async def execute_vault_strategy(
        self,
        vault_address: str,
        pool_address: str,
        positions: List[Position],
        snapshot=None,
    ):
        if not self.executor_bot_url:
            logger.error("Executor bot URL not configured")
            return

        round_id = f"miner-vault-{vault_address[:10]}-{int(time.time())}"
        positions_data = [
            {
                "tick_lower": p.tick_lower,
                "tick_upper": p.tick_upper,
                "allocation0": p.allocation0,
                "allocation1": p.allocation1,
            }
            for p in positions
        ]
        payload = {
            "api_key": self.executor_bot_api_key,
            "sn_liquidity_manager_address": vault_address,
            "pair_address": pool_address,
            "round_id": round_id,
            "positions": positions_data,
        }

        tx_hash = None
        executor_status_code = None
        error = None

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.executor_bot_url.rstrip('/')}/execute_strategy",
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=30,
                )

            executor_status_code = response.status_code
            if response.status_code != 200:
                error = response.text
                logger.error(
                    f"Executor bot error {response.status_code}: {response.text}"
                )
            else:
                data = response.json()
                tx_hash = data.get("tx_hash")
                logger.info(f"Vault strategy executed for {vault_address} tx_hash={tx_hash}")
        except Exception as e:
            error = str(e)
            logger.error(f"Executor bot request failed: {e}")

        await self.mining_repo.create_mining_execution(
            snapshot=snapshot,
            vault_address=vault_address,
            pool_address=pool_address,
            round_id=round_id,
            positions_data=positions_data,
            executor_status_code=executor_status_code,
            tx_hash=tx_hash,
            error=error,
        )

    async def run_vault_mining(self, vault_address: str):
        """
        Autonomous vault mining loop that reads on-chain state directly.
        No dependency on validator RebalanceQuery.
        """
        liq_service = self.vault_liq_services.get(vault_address)
        if liq_service is None:
            logger.warning(
                f"No on-chain service for vault {vault_address} — vault mining disabled"
            )
            return

        pool_address = liq_service.pool.address
        logger.info(
            f"Starting autonomous vault mining for {vault_address} (pool={pool_address})"
        )

        while True:
            try:
                # Read all state from chain
                current_price, tick_spacing, current_positions, inventory = (
                    await asyncio.gather(
                        liq_service.get_current_price(),
                        liq_service.get_tick_spacing(),
                        liq_service.get_current_positions(),
                        liq_service.get_inventory(),
                    )
                )

                current_tick = UniswapV3Math.get_tick_from_sqrt_price_x96(current_price)
                logger.info(
                    f"Vault {vault_address}: tick={current_tick}, "
                    f"positions={len(current_positions)}, "
                    f"inventory=({inventory.amount0}, {inventory.amount1})"
                )

                should_rebalance = False
                rebalance_reason = None
                if not current_positions:
                    should_rebalance = True
                    rebalance_reason = "no_positions"
                    logger.info(f"Vault {vault_address}: No positions, will deploy.")
                else:
                    pos = current_positions[0]
                    tick_width = pos.tick_upper - pos.tick_lower
                    buffer = tick_width * 0.2

                    if (
                        current_tick < pos.tick_lower + buffer
                        or current_tick > pos.tick_upper - buffer
                    ):
                        should_rebalance = True
                        rebalance_reason = "price_near_edge"
                        logger.info(
                            f"Vault {vault_address}: Price near edge [{pos.tick_lower}, {pos.tick_upper}]. Rebalancing."
                        )

                volatility = self.compute_volatility(self.recent_prices.get(vault_address, deque()))
                min_width = tick_spacing * 10
                computed_width = max(volatility * self.width_factor, min_width)

                new_position = None
                execution_triggered = False

                if should_rebalance:
                    inv_dict = {"amount0": inventory.amount0, "amount1": inventory.amount1}
                    new_position = self.compute_positions(
                        vault_address, current_tick, tick_spacing, inv_dict
                    )

                    if not current_positions or self._has_positions_changed(
                        [new_position], current_positions
                    ):
                        execution_triggered = True
                        logger.info(
                            f"Vault {vault_address}: Executing new position "
                            f"[{new_position.tick_lower}, {new_position.tick_upper}]"
                        )
                    else:
                        logger.info(f"Vault {vault_address}: Position unchanged, skipping.")
                else:
                    logger.info(f"Vault {vault_address}: No rebalance needed.")

                snapshot = await self.mining_repo.save_mining_snapshot(
                    vault_address=vault_address,
                    pool_address=pool_address,
                    current_tick=current_tick,
                    current_price=current_price,
                    tick_spacing=tick_spacing,
                    current_positions=current_positions,
                    inventory=inventory,
                    volatility=volatility,
                    computed_width=computed_width,
                    rebalance_reason=rebalance_reason,
                    new_position=new_position,
                    execution_triggered=execution_triggered,
                )

                if execution_triggered:
                    await self.execute_vault_strategy(
                        vault_address, pool_address, [new_position], snapshot=snapshot
                    )

            except Exception as e:
                logger.error(f"Vault mining error for {vault_address}: {e}")

            await asyncio.sleep(self.vault_executor_interval)

    def _has_positions_changed(
        self, new_positions: List[Position], current_positions: List[Position]
    ) -> bool:
        if len(new_positions) != len(current_positions):
            return True

        for new, curr in zip(new_positions, current_positions):
            if new.tick_lower != curr.tick_lower or new.tick_upper != curr.tick_upper:
                return True

        return False

    def run(self):
        """Start the miner axon server."""
        logger.info("Starting axon server...")

        self.axon.start()

        try:
            logger.info(f"Miner serving on {self.axon.ip}:{self.axon.port}")
            logger.info("Press Ctrl+C to stop")

            self.axon.serve(
                subtensor=self.subtensor,
                netuid=self.config.netuid,
            )

            bt.logging.info("Miner is running. Press Ctrl+C to stop.")

            while True:
                time.sleep(1)

        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received, stopping miner...")
            self.stop()

    def stop(self):
        """Stop the miner axon server."""
        logger.info("Stopping axon...")
        self.axon.stop()
        logger.info("Miner stopped")


def get_config():
    parser = argparse.ArgumentParser(description="SN98 ForeverMoney Miner")

    parser.add_argument(
        "--wallet.name", type=str, required=True, default="default", help="Wallet name"
    )
    parser.add_argument(
        "--wallet.hotkey",
        type=str,
        required=True,
        default="default",
        help="Wallet hotkey",
    )
    parser.add_argument(
        "--wallet.path",
        type=str,
        default=BT_WALLET_PATH,
        help="Wallet directory (default: BT_WALLET_PATH env or ~/.bittensor/wallets)",
    )

    parser.add_argument(
        "--subtensor.network",
        type=str,
        default=None,
        help=f"Subtensor network endpoint. Default: {SUBTENSOR_NETWORK}",
    )
    parser.add_argument(
        "--netuid",
        type=int,
        default=None,
        help=f"Network UID. Default: {NETUID}",
    )

    parser.add_argument(
        "--width_factor",
        type=float,
        default=3.0,
        help="Multiplier applied to volatility when computing LP width",
    )

    parser.add_argument(
        "--volatility_window",
        type=int,
        default=10,
        help="Number of recent price ticks used to compute volatility",
    )

    bt.Axon.add_args(parser)

    config = bt.Config(parser)

    if hasattr(config, "subtensor") and hasattr(config, "subtensor.network"):
        pass
    elif SUBTENSOR_NETWORK:
        config.subtensor.network = SUBTENSOR_NETWORK

    if hasattr(config, "netuid") and config.netuid is not None:
        pass
    elif NETUID:
        config.netuid = NETUID

    return config


async def main():
    config = get_config()

    logger.info(f"Config: {config}")

    wallet = bt.Wallet(config=config)
    logger.info(f"Wallet: {wallet}")

    subtensor = bt.Subtensor(config=config)
    logger.info(f"Subtensor: {subtensor}")

    miner = SN98Miner(
        wallet=wallet,
        subtensor=subtensor,
        config=config,
    )

    # Initialize DB for tracking
    try:
        db_url = f"postgres://{JOBS_POSTGRES_USER}:{JOBS_POSTGRES_PASSWORD}@{JOBS_POSTGRES_HOST}:{JOBS_POSTGRES_PORT}/{JOBS_POSTGRES_DB}"
        await init_db(db_url, schema=JOBS_POSTGRES_SCHEMA)
        logger.info("Database initialized for miner tracking")
    except Exception as e:
        logger.warning(f"Could not initialize DB — tracking disabled: {e}")

    # Discover pools and init on-chain services before starting mining loops
    await miner.init_vault_services()

    # Run Axon in a thread and vault mining(s) async
    tasks = [
        asyncio.create_task(asyncio.to_thread(miner.run)),
    ]
    for vault in miner.vault_liq_services:
        tasks.append(asyncio.create_task(miner.run_vault_mining(vault)))

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("Tasks cancelled. Shutting down miner.")
    finally:
        try:
            await close_db()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())

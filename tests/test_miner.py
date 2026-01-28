#!/usr/bin/env python3
"""
Test script to verify miner using Bittensor axon communication.

This script shows how to run and test a local miner with the new
axon/synapse communication pattern.

Usage:
    # Start a test miner (in another terminal):
    python -m miner.miner \\
        --wallet.name test_miner \\
        --wallet.hotkey default \\
        --subtensor.network local \\
        --netuid 2 \\
        --axon.port 8091

    # Then run this test script:
    python scripts/test_miner.py 127.0.0.1:8091

For testing without a local subtensor, you can use the validator's
--test-miner flag which bypasses metagraph queries:

    python -m validator.main \\
        --test-miner 127.0.0.1:8091 \\
        --pair_address 0x1024c20c048ea6087293f46d4a1c042cb6705924 \\
        --target_block 38634763 \\
        --start_block 35330091 \\
        --dry-run
"""
import sys
import os
# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import bittensor as bt
from protocol import StrategyRequest, Mode, Inventory
from validator.models import ValidatorMetadata, Constraints


def test_miner_via_dendrite(miner_address: str = "127.0.0.1:8091"):
    """
    Test miner using Bittensor dendrite (axon communication).

    Args:
        miner_address: Miner address in format "IP:PORT"
    """
    print(f"Testing miner at {miner_address} using Bittensor dendrite")
    print("=" * 60)

    # Parse miner address
    parts = miner_address.split(":")
    miner_ip = parts[0] if parts else "127.0.0.1"
    miner_port = int(parts[1]) if len(parts) > 1 else 8091

    # Create a temporary wallet for testing (or use an existing one)
    try:
        wallet = bt.wallet(name="test_validator", hotkey="default")
        print(f"Using wallet: {wallet.hotkey.ss58_address}")
    except Exception as e:
        print(f"Warning: Could not load wallet: {e}")
        print("Creating a temporary wallet for testing...")
        # For testing, we can proceed without a real wallet
        # but in production you need a proper wallet

    # Create dendrite for communication
    print("\n1. Creating dendrite for communication...")
    dendrite = bt.dendrite(wallet=wallet)

    # Create AxonInfo for the test miner
    test_miner_axon = bt.AxonInfo(
        version=4,  # Protocol version
        ip=miner_ip,
        port=miner_port,
        ip_type=4,  # IPv4
        hotkey="test_miner_hotkey",
        coldkey="test_miner_coldkey",
    )
    print(f"   Target axon: {miner_ip}:{miner_port}")

    # Create test request
    print("\n2. Creating test StrategyRequest synapse...")
    synapse = StrategyRequest(
        pair_address="0x1024c20c048ea6087293f46d4a1c042cb6705924",
        chain_id=8453,
        target_block=38634763,
        mode=Mode.INVENTORY,
        inventory=Inventory(
            amount0="1000000000000000000", amount1="2500000000"  # 1 ETH  # 2500 USDC
        ),
        current_positions=[],
        metadata=ValidatorMetadata(
            round_id="test-round-001",
            constraints=Constraints(max_il=0.10, min_tick_width=60, max_rebalances=4),
        ),
    )
    print(f"   Request created for pair: {synapse.pair_address}")
    print(f"   Target block: {synapse.target_block}")
    print(f"   Mode: {synapse.mode}")

    # Query the miner
    print("\n3. Querying miner via dendrite...")
    try:
        response = dendrite.query(
            axons=[test_miner_axon],
            synapse=synapse,
            timeout=30,
            deserialize=True,
        )[0]

        if response and response.strategy and response.miner_metadata:
            print("   ✓ Received valid response!")
            print(f"\n4. Response details:")
            print(f"   Miner version: {response.miner_metadata.version}")
            print(f"   Model info: {response.miner_metadata.model_info}")
            print(f"   Number of positions: {len(response.strategy.positions)}")
            print(
                f"   Has rebalance rule: {response.strategy.rebalance_rule is not None}"
            )

            # Validate positions
            print(f"\n5. Validating positions:")
            for i, pos in enumerate(response.strategy.positions):
                tick_width = pos.tick_upper - pos.tick_lower
                print(f"   Position {i+1}:")
                print(f"     - Tick range: [{pos.tick_lower}, {pos.tick_upper}]")
                print(f"     - Tick width: {tick_width}")
                print(f"     - Allocation0: {pos.allocation0}")
                print(f"     - Allocation1: {pos.allocation1}")

                if tick_width < 60:
                    print(f"     ⚠️  WARNING: Tick width below minimum (60)")

            return True
        else:
            print("   ✗ Miner returned invalid response")
            print(f"   Strategy: {response.strategy}")
            print(f"   Metadata: {response.miner_metadata}")
            return False

    except Exception as e:
        print(f"   ✗ Error querying miner: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ["-h", "--help"]:
        print(__doc__)
        sys.exit(0)

    miner_address = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1:8091"
    success = test_miner_via_dendrite(miner_address)
    sys.exit(0 if success else 1)

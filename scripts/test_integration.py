#!/usr/bin/env python3
"""
Integration test for SN98 ForeverMoney validator + miner.

This script tests the full flow:
1. Start local miner
2. Run validator with test miner
3. Verify results

Usage:
    python scripts/test_integration.py
"""

import subprocess
import time
import requests
import json
import sys
import os
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Configuration
MINER_PORT = 8000
MINER_URL = f"http://localhost:{MINER_PORT}"
PAIR_ADDRESS = "0x1024c20c048ea6087293f46d4a1c042cb6705924"
START_BLOCK = 35330091
TARGET_BLOCK = 38634763


def print_header(text):
    print("\n" + "=" * 60)
    print(f"  {text}")
    print("=" * 60)


def check_miner_health():
    """Check if miner is running and healthy."""
    try:
        resp = requests.get(f"{MINER_URL}/health", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            print(f"  Status: {data.get('status')}")
            print(f"  Version: {data.get('version')}")
            print(f"  Model: {data.get('model')}")
            print(f"  DB Connected: {data.get('db_connected')}")
            return True
    except requests.exceptions.ConnectionError:
        return False
    return False


def start_miner():
    """Start the miner in background."""
    print_header("Starting Miner")

    # Check if already running
    if check_miner_health():
        print("  Miner already running!")
        return None

    # Start miner
    print("  Starting miner process...")
    env = os.environ.copy()
    env["MINER_PORT"] = str(MINER_PORT)

    process = subprocess.Popen(
        ["python", "-m", "miner.miner"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(project_root),
        env=env,
    )

    # Wait for startup
    for i in range(10):
        time.sleep(1)
        if check_miner_health():
            print("  Miner started successfully!")
            return process
        print(f"  Waiting for miner... ({i+1}/10)")

    print("  ERROR: Miner failed to start!")
    process.terminate()
    return None


def run_validator():
    """Run validator with test miner."""
    print_header("Running Validator")

    cmd = [
        "python",
        "-m",
        "validator.main",
        "--wallet.name",
        "test_validator",
        "--wallet.hotkey",
        "test_hotkey",
        "--subtensor.network",
        "test",
        "--netuid",
        "98",
        "--pair_address",
        PAIR_ADDRESS,
        "--target_block",
        str(TARGET_BLOCK),
        "--start_block",
        str(START_BLOCK),
        "--test-miner",
        MINER_URL,
        "--dry-run",
    ]

    print(f"  Command: {' '.join(cmd)}")
    print()

    result = subprocess.run(
        cmd, capture_output=True, text=True, cwd=str(project_root), timeout=120
    )

    print("  STDOUT:")
    for line in result.stdout.split("\n"):
        if line.strip():
            print(f"    {line}")

    if result.stderr:
        print("\n  STDERR:")
        for line in result.stderr.split("\n"):
            if line.strip():
                print(f"    {line}")

    return result.returncode == 0


def verify_results():
    """Verify the winning strategy output."""
    print_header("Verifying Results")

    strategy_file = project_root / "winning_strategy.json"

    if not strategy_file.exists():
        print("  ERROR: winning_strategy.json not found!")
        return False

    with open(strategy_file) as f:
        strategy = json.load(f)

    print("  Winning Strategy:")
    print(f"    Miner UID: {strategy.get('winner', {}).get('miner_uid')}")
    print(f"    Final Score: {strategy.get('winner', {}).get('final_score', 0):.4f}")
    print(
        f"    Performance Score: {strategy.get('winner', {}).get('performance_score', 0):.4f}"
    )
    print(
        f"    LP Score: {strategy.get('winner', {}).get('lp_alignment_score', 0):.4f}"
    )

    positions = strategy.get("strategy", {}).get("positions", [])
    print(f"\n  Positions: {len(positions)}")
    for i, pos in enumerate(positions):
        print(f"    Position {i+1}:")
        print(f"      Tick Range: {pos.get('tick_lower')} - {pos.get('tick_upper')}")
        print(f"      Allocation0: {pos.get('allocation0')}")
        print(f"      Allocation1: {pos.get('allocation1')}")
        print(f"      Confidence: {pos.get('confidence')}")

    # Verify score is reasonable
    score = strategy.get("winner", {}).get("final_score", 0)
    if score > 0:
        print("\n  PASS: Strategy has positive score")
        return True
    else:
        print("\n  FAIL: Strategy has zero score")
        return False


def main():
    print_header("SN98 ForeverMoney Integration Test")
    print(f"  Pair: {PAIR_ADDRESS}")
    print(f"  Blocks: {START_BLOCK} - {TARGET_BLOCK}")

    miner_process = None
    success = True

    try:
        # Step 1: Start miner
        miner_process = start_miner()
        if not check_miner_health():
            print("\n  FAIL: Could not start miner")
            return 1

        # Step 2: Run validator
        if not run_validator():
            print("\n  FAIL: Validator returned error")
            success = False

        # Step 3: Verify results
        if not verify_results():
            print("\n  FAIL: Results verification failed")
            success = False

    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        success = False

    except Exception as e:
        print(f"\n  ERROR: {e}")
        success = False

    finally:
        # Cleanup
        if miner_process:
            print_header("Cleanup")
            print("  Stopping miner...")
            miner_process.terminate()
            miner_process.wait(timeout=5)
            print("  Miner stopped")

    print_header("Test Result")
    if success:
        print("  PASSED")
        return 0
    else:
        print("  FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())

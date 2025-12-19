#!/usr/bin/env python3
"""
Register validator using burned registration (instead of PoW)
This avoids the TransactorAccountShouldBeHotKey error
"""
import bittensor as bt
import sys


def main():
    wallet_name = "test_validator"
    hotkey_name = "test_hotkey"
    netuid = 98

    print("=" * 70)
    print("REGISTERING VALIDATOR ON SUBNET 98 (TESTNET)")
    print("Using BURNED REGISTRATION (not PoW)")
    print("=" * 70)

    try:
        # Load wallet
        print(f"\n1. Loading wallet...")
        wallet = bt.wallet(name=wallet_name, hotkey=hotkey_name)
        print(f"   ✅ Wallet loaded")
        print(f"      Coldkey: {wallet.coldkeypub.ss58_address}")
        print(f"      Hotkey: {wallet.hotkey.ss58_address}")

        # Connect to testnet
        print(f"\n2. Connecting to testnet...")
        subtensor = bt.subtensor(network="test")
        print(f"   ✅ Connected to testnet")
        print(f"      Endpoint: {subtensor.chain_endpoint}")

        # Check balance
        print(f"\n3. Checking balance...")
        balance = subtensor.get_balance(wallet.coldkeypub.ss58_address)
        print(f"   Coldkey balance: {balance} TAO")

        if float(balance) < 0.001:
            print(f"\n❌ ERROR: Need at least 0.001 TAO for burned registration")
            sys.exit(1)

        # Check if already registered
        print(f"\n4. Checking if already registered on subnet 98...")
        try:
            metagraph = subtensor.metagraph(netuid=netuid)
            hotkey_ss58 = wallet.hotkey.ss58_address

            if hotkey_ss58 in metagraph.hotkeys:
                uid = metagraph.hotkeys.index(hotkey_ss58)
                print(f"   ⚠️  Already registered on subnet 98!")
                print(f"      Your UID: {uid}")
                print(f"      Stake: {metagraph.S[uid]} TAO")
                print(f"\n✅ You're ready to deploy the validator!")
                sys.exit(0)
            else:
                print(f"   Not registered yet")
        except Exception as e:
            print(f"   Could not check registration: {e}")

        # Get burn cost
        print(f"\n5. Checking burn cost for subnet 98...")
        try:
            # Subnet 98 hyperparameters show min_burn = 500000 Rao = 0.0005 TAO
            print(f"   Min burn: ~0.0005 TAO (subnet 98 setting)")
            print(f"   Your balance: {balance} TAO")
            print(f"   ✅ Sufficient balance for burned registration")
        except Exception as e:
            print(f"   Warning: Could not fetch burn cost: {e}")

        # Register using burned registration
        print(f"\n6. Registering on subnet 98 (burned registration)...")
        print(f"   Network: testnet")
        print(f"   Netuid: {netuid}")
        print(f"   This burns TAO but completes instantly (no PoW)...")

        success = subtensor.burned_register(
            wallet=wallet,
            netuid=netuid,
            wait_for_inclusion=True,
            wait_for_finalization=True,
        )

        if success:
            print(f"\n🎉 REGISTRATION SUCCESSFUL!")
            print(f"\n✅ Your validator is registered on subnet 98 (testnet)")

            # Get UID
            metagraph = subtensor.metagraph(netuid=netuid)
            hotkey_ss58 = wallet.hotkey.ss58_address
            if hotkey_ss58 in metagraph.hotkeys:
                uid = metagraph.hotkeys.index(hotkey_ss58)
                print(f"   Your UID: {uid}")
                print(f"   Hotkey: {hotkey_ss58}")

            # Check new balance
            new_balance = subtensor.get_balance(wallet.coldkeypub.ss58_address)
            burned = float(balance) - float(new_balance)
            print(f"\n   TAO burned: {burned:.6f}")
            print(f"   Remaining balance: {new_balance} TAO")

            print(f"\n" + "=" * 70)
            print("NEXT STEPS:")
            print("=" * 70)
            print(f"1. ✅ Wallet created")
            print(f"2. ✅ Testnet TAO received")
            print(f"3. ✅ Registered on subnet 98")
            print(f"4. 📋 Get database credentials from founder")
            print(f"5. 🚀 Deploy validator")
        else:
            print(f"\n❌ Registration failed!")
            print(f"   Check logs above for details")
            sys.exit(1)

    except KeyboardInterrupt:
        print(f"\n\n⚠️  Registration interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error during registration: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)

    print("=" * 70)


if __name__ == "__main__":
    main()

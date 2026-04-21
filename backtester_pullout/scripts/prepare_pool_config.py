"""Fill pool metadata (token0/1, decimals, fee, tick_spacing) via Base RPC.

Reads a YAML config where each pool has at least `address` and `symbol`
(other metadata fields may be placeholders), queries the pool + token contracts
on Base, and writes a fully-populated YAML to <input>.generated.yaml.

Usage:
  python -m backtester_pullout.scripts.prepare_pool_config \
      backtester_pullout/config/example.yaml
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv
from web3 import Web3


# Minimal ABIs — only the methods we call.
POOL_ABI = [
    {"name": "token0", "inputs": [], "outputs": [{"type": "address"}], "stateMutability": "view", "type": "function"},
    {"name": "token1", "inputs": [], "outputs": [{"type": "address"}], "stateMutability": "view", "type": "function"},
    {"name": "fee", "inputs": [], "outputs": [{"type": "uint24"}], "stateMutability": "view", "type": "function"},
    {"name": "tickSpacing", "inputs": [], "outputs": [{"type": "int24"}], "stateMutability": "view", "type": "function"},
]
ERC20_ABI = [
    {"name": "decimals", "inputs": [], "outputs": [{"type": "uint8"}], "stateMutability": "view", "type": "function"},
    {"name": "symbol", "inputs": [], "outputs": [{"type": "string"}], "stateMutability": "view", "type": "function"},
]


def fetch_pool_metadata(w3: Web3, pool_addr: str) -> dict:
    pool = w3.eth.contract(address=Web3.to_checksum_address(pool_addr), abi=POOL_ABI)
    token0 = pool.functions.token0().call()
    token1 = pool.functions.token1().call()
    fee = pool.functions.fee().call()
    tick_spacing = pool.functions.tickSpacing().call()

    t0 = w3.eth.contract(address=token0, abi=ERC20_ABI)
    t1 = w3.eth.contract(address=token1, abi=ERC20_ABI)
    decimals0 = t0.functions.decimals().call()
    decimals1 = t1.functions.decimals().call()
    try:
        sym0 = t0.functions.symbol().call()
    except Exception:
        sym0 = "?"
    try:
        sym1 = t1.functions.symbol().call()
    except Exception:
        sym1 = "?"

    return {
        "token0": token0,
        "token1": token1,
        "decimals0": int(decimals0),
        "decimals1": int(decimals1),
        "fee_tier": int(fee),
        "tick_spacing": int(tick_spacing),
        "_symbol0": sym0,
        "_symbol1": sym1,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="Input YAML config")
    parser.add_argument(
        "--out", type=Path, default=None,
        help="Output path (default: <config>.generated.yaml)",
    )
    parser.add_argument("--rpc", default=None, help="Override BASE_RPC env var")
    args = parser.parse_args(argv)

    load_dotenv()
    rpc = args.rpc or os.environ.get("BASE_RPC")
    if not rpc:
        print("ERROR: BASE_RPC not set in env and --rpc not provided", file=sys.stderr)
        return 2

    w3 = Web3(Web3.HTTPProvider(rpc))
    if not w3.is_connected():
        print(f"ERROR: cannot connect to {rpc}", file=sys.stderr)
        return 2

    with args.config.open() as f:
        cfg = yaml.safe_load(f)

    for pool in cfg.get("pools", []):
        addr = pool["address"]
        print(f"Fetching metadata for {addr}...")
        meta = fetch_pool_metadata(w3, addr)
        sym0 = meta.pop("_symbol0")
        sym1 = meta.pop("_symbol1")
        pool.update(meta)
        pool["symbol"] = f"{sym0}/{sym1}"  # always overwrite with fetched symbols
        print(
            f"  {sym0}/{sym1}  token0={meta['token0']} (dec {meta['decimals0']}), "
            f"token1={meta['token1']} (dec {meta['decimals1']}), "
            f"fee={meta['fee_tier']}, tick_spacing={meta['tick_spacing']}"
        )

    out = args.out or args.config.with_suffix(".generated.yaml")
    with out.open("w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

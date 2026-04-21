"""Regenerate equity.png and report.html from an existing results dir.

Avoids re-running the full backtest. Rebuilds the VolOracle from swaps
(cheap compared to the engine loop).

Usage:
    BACKTESTER_DB_URL=... python -m backtester_pullout.scripts.replot <results_dir>
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pandas as pd

from backtester_pullout.backtester.data import load_swaps
from backtester_pullout.backtester.engine import EngineResult, HodlLeg, LpLeg
from backtester_pullout.backtester.plots import equity_curve
from backtester_pullout.backtester.report import write_report
from backtester_pullout.backtester.vol import VolOracle, make_rng


async def main(results_dir: str) -> int:
    d = Path(results_dir)
    manifest = json.loads((d / "manifest.json").read_text())
    pool_d = manifest["pool"]
    backtest = manifest["backtest"]
    prediction = manifest["prediction"]
    strategy_cfg = manifest.get("strategy", {})
    seed = manifest.get("seed", 42)

    # Load swaps + rebuild oracle
    print(f"Loading swaps for {pool_d['symbol']}...")
    swaps = await load_swaps(pool_d["address"], backtest["start_block"], backtest["end_block"])
    print(f"  {len(swaps):,} swaps.")
    oracle = VolOracle.build(
        swaps,
        bucket_blocks=prediction["vol_bucket_blocks"],
        horizon_blocks=prediction["horizon_blocks"],
        noise_sigma=prediction["noise_sigma"],
        rng=make_rng(seed, 0),
    )

    # Load equity + actions from disk
    equity = pd.read_csv(d / "equity.csv")
    # equity CSVs store hodl/passive/strategy in human units; engine plotting
    # expects raw (scaled by 10**decimals1). Convert back.
    scale = 10 ** pool_d["decimals1"]
    for col in ("hodl", "passive", "strategy"):
        equity[col] = (equity[col] * scale).astype("int64")

    actions_path = d / "actions.csv"
    actions = []
    if actions_path.exists():
        actions = pd.read_csv(actions_path).to_dict("records")

    # Rebuild a minimal PoolConfig-shaped object for the plot
    class _Pool:
        symbol = pool_d["symbol"]
        address = pool_d["address"]
        decimals0 = pool_d["decimals0"]
        decimals1 = pool_d["decimals1"]

    res = EngineResult(
        equity=equity,
        actions=actions,
        final_passive=LpLeg(),
        final_strategy=LpLeg(),
        final_hodl=HodlLeg(0, 0),
        vol_oracle=oracle,
    )

    out_png = d / "equity.png"
    equity_curve(res, _Pool(), out_png,
                 strategy_params=strategy_cfg.get("params", {}))
    print(f"Wrote {out_png}")

    report = write_report(d)
    print(f"Wrote {report}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(main(sys.argv[1])))

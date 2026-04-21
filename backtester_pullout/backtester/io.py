"""Output writing: timestamped results dirs, CSVs, JSON manifests.

No silent overwrites — if a target dir exists we bail.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import pandas as pd

from backtester_pullout.backtester.engine import EngineResult


def make_results_dir(base: str | Path = "results", label: str = "run") -> Path:
    """Create results/<timestamp>_<label>/ and return it. Raises if exists."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    p = Path(base) / f"{ts}_{label}"
    p.mkdir(parents=True, exist_ok=False)  # refuses to overwrite
    return p


def write_equity_csv(res: EngineResult, out_dir: Path, *, decimals1: int) -> Path:
    """Write equity time series as CSV (token1 human units for readability)."""
    scale = 10 ** decimals1
    df = res.equity.copy()
    for col in ("hodl", "passive", "strategy"):
        df[col] = df[col].astype("float64") / scale
    path = out_dir / "equity.csv"
    df.to_csv(path, index=False)
    return path


def write_actions_csv(res: EngineResult, out_dir: Path) -> Path:
    path = out_dir / "actions.csv"
    pd.DataFrame(res.actions).to_csv(path, index=False)
    return path


def write_metrics_json(metrics: Dict[str, Dict[str, float]], out_dir: Path) -> Path:
    path = out_dir / "metrics.json"
    with path.open("w") as f:
        json.dump(metrics, f, indent=2, default=float)
    return path


def write_manifest(manifest: Dict[str, Any], out_dir: Path, name: str = "manifest.json") -> Path:
    path = out_dir / name
    with path.open("w") as f:
        json.dump(manifest, f, indent=2, default=str)
    return path

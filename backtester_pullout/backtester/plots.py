"""Plot helpers for single-run and sweep outputs. Matplotlib-only, non-interactive.

Single-run:
  - equity_curve(res, pool, out_path) — 3 benchmarks on one axis.

Sweep:
  - heatmap(df, x_col, y_col, value_col, out_path)
  - breakeven_curve(df, horizon_col, threshold_col, noise_col, value_col, out_path)
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd  # noqa: F401

from backtester_pullout.backtester.config import PoolConfig
from backtester_pullout.backtester.engine import EngineResult


def equity_curve(res: EngineResult, pool: PoolConfig, out_path: Path,
                 *, strategy_params: Optional[dict] = None) -> Path:
    """3-panel chart sharing x-axis (block):
      1. price (token1/token0, human units) with action markers
      2. equity (HODL / Passive / Strategy) with action markers
      3. predicted + realized vol, with threshold lines if available
    """
    scale = 10 ** pool.decimals1
    eq = res.equity
    actions = res.actions or []

    # High-contrast palette — black / blue / orange on white is robust even
    # for colorblindness (matches matplotlib's qualitative "colorblind" set).
    C_PRICE   = "#111111"
    C_HODL    = "#000000"
    C_PASSIVE = "#1f77b4"   # strong blue
    C_STRAT   = "#ff7f0e"   # strong orange
    C_REAL    = "#000000"   # realized vol: solid black
    C_PRED    = "#d62728"   # predicted vol: red, dashed
    C_ENTER   = "#17a34a"   # green
    C_EXIT    = "#dc2626"   # red

    fig, (ax_p, ax_eq, ax_v) = plt.subplots(
        3, 1, figsize=(14, 11), sharex=True,
        gridspec_kw={"height_ratios": [1.0, 1.4, 1.0]},
    )

    # -- Split actions (skip initial) -----------------------------------------
    enter_blocks, exit_blocks = [], []
    for a in actions:
        if a.get("reason") == "initial":
            continue
        if a["action"] == "ENTER":
            enter_blocks.append(a["block"])
        elif a["action"] == "EXIT":
            exit_blocks.append(a["block"])

    # Lookup (block → index) for marker y-values.
    blk_to_idx = {int(b): i for i, b in enumerate(eq["block"].to_numpy())}

    def _markers_on(ax, blocks, y_series, color, marker, label):
        if not blocks:
            return
        xs, ys = [], []
        for b in blocks:
            i = blk_to_idx.get(int(b))
            if i is None:
                continue
            xs.append(b)
            ys.append(y_series[i])
        # translucent vertical line for context
        for b in xs:
            ax.axvline(b, color=color, alpha=0.25, linewidth=0.8)
        ax.scatter(xs, ys, marker=marker, s=70, c=color,
                   edgecolors="white", linewidths=1.2, zorder=5, label=label)

    # -- Panel 1: price --------------------------------------------------------
    ax_p.plot(eq["block"], eq["price"], color=C_PRICE, linewidth=1.1, label="price")
    ax_p.set_ylabel(f"price ({pool.symbol.split('/')[-1]} per "
                    f"{pool.symbol.split('/')[0]})",
                    fontsize=10)
    ax_p.set_title(f"{pool.symbol}  ·  {pool.address}", fontsize=11)
    ax_p.grid(True, alpha=0.25)

    price_arr = eq["price"].to_numpy()
    _markers_on(ax_p, exit_blocks, price_arr, C_EXIT, "v", "EXIT")
    _markers_on(ax_p, enter_blocks, price_arr, C_ENTER, "^", "ENTER")
    ax_p.legend(loc="best", fontsize=9)

    # -- Panel 2: equity -------------------------------------------------------
    hodl_arr = (eq["hodl"] / scale).to_numpy()
    passive_arr = (eq["passive"] / scale).to_numpy()
    strat_arr = (eq["strategy"] / scale).to_numpy()

    ax_eq.plot(eq["block"], hodl_arr, label="HODL 50/50",
               color=C_HODL, linewidth=1.3)
    ax_eq.plot(eq["block"], passive_arr, label="Passive LP",
               color=C_PASSIVE, linewidth=2.2, linestyle="--", alpha=0.95)
    ax_eq.plot(eq["block"], strat_arr, label="Strategy",
               color=C_STRAT, linewidth=1.4, alpha=0.95)
    ax_eq.set_ylabel(f"value ({pool.symbol.split('/')[-1]})", fontsize=10)
    ax_eq.grid(True, alpha=0.25)

    _markers_on(ax_eq, exit_blocks, strat_arr, C_EXIT, "v", "EXIT")
    _markers_on(ax_eq, enter_blocks, strat_arr, C_ENTER, "^", "ENTER")
    ax_eq.legend(loc="best", fontsize=9)

    # -- Panel 3: vol ----------------------------------------------------------
    # With 100k+ buckets, two overlapping lines are unreadable. We:
    #   1. Subsample to ~2000 points per series (keeps detail, fits pixels)
    #   2. Draw realized as a filled area (light gray) for context
    #   3. Draw predicted as a thin solid line on top, full opacity
    oracle = res.vol_oracle
    if oracle is not None:
        ends = np.asarray(oracle.bucket_ends)
        real = np.asarray(oracle.realized, dtype=float)
        pred = np.asarray(oracle.predicted, dtype=float)

        # Drop NaNs (tail where horizon doesn't fit)
        mask = np.isfinite(real)
        ends_r, real_r = ends[mask], real[mask]
        mask_p = np.isfinite(pred)
        ends_p, pred_p = ends[mask_p], pred[mask_p]
        # Clip negative predicted (noise artifact)
        pred_p = np.clip(pred_p, 0, None)

        def _subsample(x, y, target=2000):
            if len(x) <= target:
                return x, y
            step = len(x) // target
            return x[::step], y[::step]

        xr, yr = _subsample(ends_r, real_r)
        xp, yp = _subsample(ends_p, pred_p)

        # Realized as filled area (the "ground truth" landscape)
        ax_v.fill_between(xr, 0, yr, color="#1f77b4", alpha=0.45,
                          label="realized vol", linewidth=0, zorder=1)
        # Predicted as a crisp line on top
        ax_v.plot(xp, yp, color=C_PRED, linewidth=1.2, alpha=1.0,
                  label=f"predicted vol (σ={oracle.noise_sigma})",
                  zorder=3)

    ax_v.set_ylabel("vol  (std of log-ret / bucket)", fontsize=10)
    ax_v.set_xlabel("block", fontsize=10)
    ax_v.grid(True, alpha=0.25)

    if strategy_params:
        for key, label, color in [
            ("threshold_high", "threshold_high", "#b1241c"),
            ("threshold_low",  "threshold_low",  "#197a26"),
            ("threshold",      "threshold",      "#555555"),
        ]:
            v = strategy_params.get(key)
            if v is not None:
                ax_v.axhline(v, color=color, alpha=0.75, linestyle=":",
                             linewidth=1.3, label=label)
    handles, _ = ax_v.get_legend_handles_labels()
    if handles:
        ax_v.legend(loc="best", fontsize=9)

    fig.tight_layout()
    fig.savefig(out_path, dpi=200)
    plt.close(fig)
    return out_path


def heatmap(
    df: pd.DataFrame,
    x_col: str,
    y_col: str,
    value_col: str,
    out_path: Path,
    *,
    title: Optional[str] = None,
    cmap: str = "RdYlGn",
) -> Path:
    """Pivot `df` on (x_col, y_col) → value_col grid, render as heatmap.

    Skipped gracefully if `df` has zero rows or the pivot is fully NaN.
    """
    if len(df) == 0:
        return out_path
    pivot = df.pivot_table(index=y_col, columns=x_col, values=value_col, aggfunc="mean")
    fig, ax = plt.subplots(figsize=(max(6, len(pivot.columns) * 0.8),
                                    max(4, len(pivot.index) * 0.5)))
    im = ax.imshow(pivot.values, aspect="auto", cmap=cmap, origin="lower")

    ax.set_xticks(range(len(pivot.columns)))
    ax.set_xticklabels([str(c) for c in pivot.columns], rotation=45, ha="right")
    ax.set_yticks(range(len(pivot.index)))
    ax.set_yticklabels([str(i) for i in pivot.index])
    ax.set_xlabel(x_col)
    ax.set_ylabel(y_col)
    ax.set_title(title or f"{value_col} by {x_col} × {y_col}")

    # annotate each cell
    for i in range(pivot.shape[0]):
        for j in range(pivot.shape[1]):
            v = pivot.values[i, j]
            if np.isnan(v):
                continue
            ax.text(j, i, f"{v:.3f}", ha="center", va="center",
                    fontsize=8, color="black")

    fig.colorbar(im, ax=ax, label=value_col)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path


def breakeven_curve(
    df: pd.DataFrame,
    noise_col: str,
    value_col: str,
    group_col: str,
    out_path: Path,
    *,
    title: Optional[str] = None,
) -> Path:
    """For each group (e.g. horizon or threshold), plot excess_return vs noise.

    Zero-crossing of each curve is the break-even noise level.
    """
    if len(df) == 0:
        return out_path
    fig, ax = plt.subplots(figsize=(10, 6))
    for name, g in df.groupby(group_col):
        g = g.sort_values(noise_col)
        ax.plot(g[noise_col], g[value_col], marker="o", label=f"{group_col}={name}")

    ax.axhline(0, color="k", linewidth=0.8, alpha=0.5)
    ax.set_xlabel(noise_col)
    ax.set_ylabel(value_col)
    ax.set_title(title or f"{value_col} vs {noise_col} (per {group_col})")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path

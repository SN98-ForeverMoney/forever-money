"""Render heatmaps + HTML report for a megasweep results dir."""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def _pivot(df: pd.DataFrame, period_days: int, value: str) -> pd.DataFrame:
    sub = df[df["days"] == period_days]
    return sub.pivot(index="noise_sigma", columns="slippage_bps", values=value)


def _heatmap_png(pivot: pd.DataFrame, title: str, out_path: Path, fmt: str = "{:.2%}"):
    fig, ax = plt.subplots(figsize=(6, 4))
    im = ax.imshow(pivot.values, aspect="auto", cmap="RdYlGn", origin="lower")
    ax.set_xticks(range(len(pivot.columns)))
    ax.set_xticklabels([f"{c:g} bps" for c in pivot.columns])
    ax.set_yticks(range(len(pivot.index)))
    ax.set_yticklabels([f"σ={s}" for s in pivot.index])
    ax.set_xlabel("extra slippage (bps)")
    ax.set_ylabel("noise sigma")
    ax.set_title(title, fontsize=11)
    for i in range(pivot.shape[0]):
        for j in range(pivot.shape[1]):
            v = pivot.values[i, j]
            if np.isnan(v):
                continue
            ax.text(j, i, fmt.format(v), ha="center", va="center",
                    fontsize=9, color="black")
    fig.colorbar(im, ax=ax)
    fig.tight_layout()
    fig.savefig(out_path, dpi=180)
    plt.close(fig)


def _img64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def main(results_dir: str) -> int:
    d = Path(results_dir)
    df = pd.read_csv(d / "megasweep.csv")
    plan = json.loads((d / "plan.json").read_text())
    pool_sym = plan["pool"]["symbol"]
    pool_addr = plan["pool"]["address"]

    # Generate heatmaps for each period
    periods = sorted(df["days"].unique())
    heatmap_paths = {}
    for p_days in periods:
        hodl_r = df[df["days"] == p_days].iloc[0]["hodl_return"]
        piv = _pivot(df, p_days, "excess_return_vs_hodl")
        png = d / f"heatmap_excess_{p_days}d.png"
        _heatmap_png(piv, f"{p_days}d — excess return vs HODL ({hodl_r:+.2%})", png)
        heatmap_paths[p_days] = png

    # Strategy-return heatmaps too
    strat_heatmap_paths = {}
    for p_days in periods:
        piv = _pivot(df, p_days, "strategy_return")
        png = d / f"heatmap_strategy_{p_days}d.png"
        _heatmap_png(piv, f"{p_days}d — strategy absolute return", png)
        strat_heatmap_paths[p_days] = png

    # Best cells table
    best_rows = []
    for p_days in periods:
        best = df[df["days"] == p_days].sort_values(
            "excess_return_vs_hodl", ascending=False).head(1).iloc[0]
        best_rows.append({
            "period": f"{p_days}d",
            "noise": best["noise_sigma"],
            "slip_bps": best["slippage_bps"],
            "strategy": f"{best['strategy_return']:+.2%}",
            "hodl": f"{best['hodl_return']:+.2%}",
            "excess": f"{best['excess_return_vs_hodl']:+.2%}",
            "actions": f"{int(best['n_enter'])}E/{int(best['n_exit'])}X/"
                       f"{int(best['n_rebalance'])}R",
            "time_in_mkt": f"{best['strategy_tim']:.1%}",
            "costs": f"{best['strategy_costs']:.2f}",
        })
    best_df = pd.DataFrame(best_rows)

    # Full table: format percentages
    full = df.copy()
    pct_cols = ["hodl_return", "passive_return", "strategy_return",
                "excess_return_vs_hodl", "strategy_max_dd", "strategy_tim"]
    for c in pct_cols:
        full[c] = full[c].map(lambda x: f"{x:+.4%}")
    full["strategy_sharpe"] = full["strategy_sharpe"].round(3)
    full["strategy_costs"] = full["strategy_costs"].round(3)

    css = """<style>
      body { font-family: system-ui, sans-serif; max-width: 1200px; margin: 24px auto;
             padding: 0 20px; color: #222; }
      h1 { border-bottom: 2px solid #333; padding-bottom: 8px; }
      h2 { color: #2a4a7f; margin-top: 36px; border-bottom: 1px solid #ccc; padding-bottom: 4px; }
      table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 13px; }
      th, td { border: 1px solid #ddd; padding: 5px 8px; text-align: right; }
      th { background: #f3f3f3; text-align: center; }
      tr:nth-child(even) td { background: #fafafa; }
      img { max-width: 100%; border: 1px solid #ddd; border-radius: 6px; margin: 8px 0; }
      .small { font-size: 12px; color: #666; }
      details { margin: 12px 0; }
      summary { cursor: pointer; font-weight: 600; color: #2a4a7f; }
      pre { background: #f6f6f6; padding: 12px; border-radius: 6px; font-size: 12px; overflow-x: auto; }
    </style>"""

    html = [f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Megasweep — {pool_sym}</title>{css}</head><body>"]
    html.append(f"<h1>Megasweep — {pool_sym}</h1>")
    html.append(f'<p class="small">Pool: {pool_addr} · {len(df)} cells ·'
                f' grid: {sorted(df["days"].unique())}d × noise {sorted(df["noise_sigma"].unique())}'
                f' × slip {sorted(df["slippage_bps"].unique())} bps</p>')

    html.append("<h2>Best cell per period</h2>")
    html.append(best_df.to_html(index=False, classes="data", escape=False))

    html.append("<h2>Heatmaps — excess return vs HODL</h2>")
    for p_days in periods:
        html.append(f"<h3>{p_days}d</h3>")
        html.append(f'<img src="data:image/png;base64,{_img64(heatmap_paths[p_days])}">')

    html.append("<h2>Heatmaps — strategy absolute return</h2>")
    for p_days in periods:
        html.append(f"<h3>{p_days}d</h3>")
        html.append(f'<img src="data:image/png;base64,{_img64(strat_heatmap_paths[p_days])}">')

    html.append("<h2>All 36 cells</h2>")
    html.append(full[[
        "cell_index", "days", "noise_sigma", "slippage_bps",
        "hodl_return", "passive_return", "strategy_return", "excess_return_vs_hodl",
        "strategy_sharpe", "strategy_max_dd", "strategy_tim",
        "n_enter", "n_exit", "n_rebalance", "strategy_costs",
    ]].to_html(index=False, escape=False))

    html.append("<details><summary>Plan (original config)</summary>")
    html.append(f'<pre>{json.dumps(plan, indent=2, default=str)}</pre></details>')
    html.append("</body></html>")

    out = d / "report.html"
    out.write_text("\n".join(html))
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1
                  else "results/20260414_230602_megasweep"))

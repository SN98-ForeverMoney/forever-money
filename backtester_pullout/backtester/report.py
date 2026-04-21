"""Self-contained HTML report generator.

For a results dir produced by run_backtest.py or run_sweep.py, write a
report.html with all images base64-embedded → one shareable file.

Detects single-run vs sweep by which files are present:
  single: equity.csv, metrics.json, actions.csv, equity.png, manifest.json
  sweep:  sweep.csv, cells.json, heatmap_*.png, breakeven.png, manifest.json
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Iterable

import pandas as pd


def _img_tag(path: Path, *, alt: str = "") -> str:
    if not path.exists():
        return ""
    data = base64.b64encode(path.read_bytes()).decode()
    return (
        f'<img alt="{alt}" '
        f'style="max-width:100%;border:1px solid #ddd;border-radius:6px;'
        f'margin:8px 0;" src="data:image/png;base64,{data}">'
    )


def _table_from_df(df: pd.DataFrame, *, max_rows: int | None = None) -> str:
    if max_rows is not None and len(df) > max_rows:
        df = df.head(max_rows)
    return df.to_html(index=False, classes="data", border=0, float_format="%.6g")


def _table_from_dict(d: dict) -> str:
    rows = "".join(
        f"<tr><th>{k}</th><td>{v}</td></tr>"
        for k, v in d.items()
    )
    return f'<table class="kv">{rows}</table>'


_CSS = """
<style>
  body { font-family: system-ui, sans-serif; max-width: 1100px; margin: 24px auto;
         padding: 0 20px; color: #222; }
  h1 { border-bottom: 2px solid #333; padding-bottom: 8px; }
  h2 { color: #2a4a7f; margin-top: 36px; border-bottom: 1px solid #ccc;
       padding-bottom: 4px; }
  table { border-collapse: collapse; margin: 8px 0; }
  table.data { width: 100%; font-size: 13px; }
  table.data th, table.data td { border: 1px solid #ddd; padding: 5px 8px;
                                  text-align: right; }
  table.data th { background: #f3f3f3; text-align: center; }
  table.data tr:nth-child(even) td { background: #fafafa; }
  table.kv th { text-align: right; padding: 4px 12px 4px 0; color: #555;
                font-weight: 600; vertical-align: top; }
  table.kv td { padding: 4px 0; font-family: ui-monospace, monospace; }
  .pos { color: #197a26; font-weight: 600; }
  .neg { color: #b1241c; font-weight: 600; }
  .small { font-size: 12px; color: #666; }
  pre { background: #f6f6f6; padding: 12px; border-radius: 6px;
        overflow-x: auto; font-size: 12px; }
  details { margin: 12px 0; }
  summary { cursor: pointer; font-weight: 600; color: #2a4a7f; }
</style>
"""


def _signed(v: float, pct: bool = False) -> str:
    fmt = f"{v:+.4%}" if pct else f"{v:+,.6g}"
    cls = "pos" if v > 0 else ("neg" if v < 0 else "")
    return f'<span class="{cls}">{fmt}</span>'


# ---------------------------------------------------------------------------
# Single-run report
# ---------------------------------------------------------------------------
def _render_single(results_dir: Path) -> str:
    equity = pd.read_csv(results_dir / "equity.csv")
    actions = pd.read_csv(results_dir / "actions.csv") if (results_dir / "actions.csv").exists() else pd.DataFrame()
    metrics = json.loads((results_dir / "metrics.json").read_text())
    manifest = json.loads((results_dir / "manifest.json").read_text())

    pool = manifest.get("pool", {})
    excess = manifest.get("excess_return_vs_hodl", 0.0)

    metrics_table = pd.DataFrame(metrics).T  # legs as rows, metrics as cols
    metrics_table_html = _table_from_df(metrics_table.reset_index().rename(columns={"index": "leg"}))

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Backtest report — {pool.get('symbol', '')}</title>
{_CSS}</head><body>
<h1>Backtest report — {pool.get('symbol', '?')}</h1>
<p class="small">{results_dir.name} · pool {pool.get('address', '')} · seed {manifest.get('seed', '?')}</p>

<h2>Headline</h2>
{_table_from_dict({
    "Strategy": manifest.get("strategy", {}).get("type", "?"),
    "Excess return vs HODL": _signed(excess, pct=True),
    "Strategy final": f"{metrics['strategy']['final']:,.4f}",
    "HODL final": f"{metrics['hodl']['final']:,.4f}",
    "Passive LP final": f"{metrics['passive']['final']:,.4f}",
    "Rebalances": int(metrics['strategy']['num_rebalances']),
    "Total costs paid": f"{metrics['strategy']['total_costs']:.6f}",
})}

<h2>Per-leg metrics</h2>
{metrics_table_html}

<h2>Equity curves</h2>
{_img_tag(results_dir / "equity.png", alt="equity curves")}

<h2>Action log</h2>
{('<p class="small">No actions recorded.</p>' if actions.empty else _table_from_df(actions))}

<details><summary>Manifest (full config)</summary>
<pre>{json.dumps(manifest, indent=2, default=str)}</pre>
</details>

</body></html>"""


# ---------------------------------------------------------------------------
# Sweep report
# ---------------------------------------------------------------------------
def _render_sweep(results_dir: Path) -> str:
    sweep_df = pd.read_csv(results_dir / "sweep.csv")
    manifest = json.loads((results_dir / "manifest.json").read_text())
    cells = json.loads((results_dir / "cells.json").read_text())

    grid_keys = manifest.get("grid_keys", [])
    pool = manifest.get("pool", {})

    best = sweep_df.loc[sweep_df["excess_return_vs_hodl"].idxmax()]
    worst = sweep_df.loc[sweep_df["excess_return_vs_hodl"].idxmin()]

    # Trim sweep table to the columns that matter
    display_cols = (
        ["cell_index", "excess_return_vs_hodl"] + grid_keys +
        ["strategy.total_return", "strategy.sharpe", "strategy.max_drawdown",
         "strategy.num_rebalances", "strategy.total_costs"]
    )
    display_cols = [c for c in display_cols if c in sweep_df.columns]
    sweep_view = sweep_df[display_cols].sort_values("excess_return_vs_hodl", ascending=False)

    # All heatmap PNGs in the dir
    heatmaps = sorted(results_dir.glob("heatmap_*.png"))
    heatmap_html = "\n".join(
        f"<h3>{p.stem.replace('heatmap_', '').replace('_', ' ')}</h3>{_img_tag(p)}"
        for p in heatmaps
    )

    breakeven_html = ""
    if (results_dir / "breakeven.png").exists():
        breakeven_html = f"<h3>Break-even by horizon</h3>{_img_tag(results_dir / 'breakeven.png')}"

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Sweep report — {pool.get('symbol', '?')}</title>
{_CSS}</head><body>
<h1>Sweep report — {pool.get('symbol', '?')}</h1>
<p class="small">{results_dir.name} · {len(cells)} cells · base seed {manifest.get('base_seed', '?')}</p>

<h2>Headline</h2>
{_table_from_dict({
    "Cells run": len(cells),
    "Best cell": int(best['cell_index']),
    "Best excess return vs HODL": _signed(float(best['excess_return_vs_hodl']), pct=True),
    "Worst excess return vs HODL": _signed(float(worst['excess_return_vs_hodl']), pct=True),
    "Median excess return": _signed(float(sweep_df['excess_return_vs_hodl'].median()), pct=True),
    "Cells beating HODL": f"{int((sweep_df['excess_return_vs_hodl'] > 0).sum())} / {len(sweep_df)}",
})}

<h2>Best cell parameters</h2>
{_table_from_dict({k: best[k] for k in grid_keys if k in best})}

<h2>All cells (sorted by excess return)</h2>
{_table_from_df(sweep_view)}

<h2>Heatmaps</h2>
{heatmap_html or '<p class="small">No heatmaps generated.</p>'}

{breakeven_html}

<details><summary>Manifest</summary>
<pre>{json.dumps(manifest, indent=2, default=str)}</pre>
</details>

</body></html>"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def write_report(results_dir: str | Path) -> Path:
    """Detect run type and write report.html into results_dir.

    Returns the path to the report.
    """
    results_dir = Path(results_dir)
    if (results_dir / "sweep.csv").exists():
        html = _render_sweep(results_dir)
    elif (results_dir / "equity.csv").exists():
        html = _render_single(results_dir)
    else:
        raise ValueError(
            f"{results_dir} doesn't look like a backtest output dir "
            "(missing both equity.csv and sweep.csv)"
        )
    out = results_dir / "report.html"
    out.write_text(html)
    return out

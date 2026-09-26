"""Render the static dashboard: one HTML page, local Plotly, CSS and JS, and the CSV export.

The output directory is self-contained (no CDN, no secrets), so GitHub Pages can serve it
as-is and it also works when opened from disk.
"""

from __future__ import annotations

import json
import math
import shutil
from importlib import resources
from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader
from plotly.offline import get_plotlyjs

from gridwatch.dashboard.data import DashboardData, collect
from gridwatch.dashboard.figures import all_charts


def _clean(value: Any) -> Any:
    """Replace NaN and infinities with None so the output is strict JSON."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_clean(v) for v in value]
    return value


def _json_for_script(value: Any) -> str:
    """JSON safe to embed inside a <script> element."""
    text = json.dumps(_clean(value), separators=(",", ":"), allow_nan=False)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _fmt(value: Any, digits: int = 0, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    return f"{value:,.{digits}f}{suffix}"


def kpi_tiles(data: DashboardData) -> list[dict[str, str]]:
    k = data.kpis
    tiles = [
        {
            "label": "Last 7 days, GB mean",
            "value": _fmt(k.get("last_7d_mean")),
            "unit": "gCO2/kWh",
            "detail": f"Report window mean {_fmt(k.get('window_mean'))} gCO2/kWh",
        },
        {
            "label": f"Best start for a {data.report_window['batch_job_hours']}-hour job",
            "value": str(k.get("best_start") or "n/a"),
            "unit": "UK time",
            "detail": f"{_fmt(k.get('best_start_mean'))} against "
            f"{_fmt(k.get('worst_start_mean'))} gCO2/kWh at {k.get('worst_start')}",
        },
        {
            "label": "Saving from a simple rule",
            "value": _fmt(k.get("profile_saving_vs_0900_pct"), 0, "%"),
            "unit": "vs 09:00 daily",
            "detail": "Start at the slot that was lowest over the previous 28 days "
            f"({_fmt(k.get('profile_saving_vs_1700_pct'), 0, '%')} vs 17:00)",
        },
        {
            "label": "API forecast error",
            "value": _fmt(k.get("api_mae"), 1),
            "unit": "gCO2/kWh MAE",
            "detail": f"MAPE {_fmt(k.get('api_mape'), 1, '%')}; short-lead forecast",
        },
    ]
    model = next(
        (
            m
            for m in data.backtest_metrics
            if m["method"] == "model" and m["horizon_band"] == "24-48 h"
        ),
        None,
    )
    naive = next(
        (
            m
            for m in data.backtest_metrics
            if m["method"] == "naive_yesterday" and m["horizon_band"] == "24-48 h"
        ),
        None,
    )
    if model and naive:
        tiles.append(
            {
                "label": "Model error, 24-48 h ahead",
                "value": _fmt(model["mae"], 1),
                "unit": "gCO2/kWh MAE",
                "detail": f"Same half-hour, last known day: {_fmt(naive['mae'], 1)}",
            }
        )
    return tiles


def render(data: DashboardData, out_dir: Path, annual_csv: Path | None = None) -> Path:
    # Always escape: select_autoescape(["html"]) would not match "index.html.j2". The chart
    # JSON is escaped for its <script> element separately and marked safe in the template.
    env = Environment(
        loader=PackageLoader("gridwatch.dashboard", "templates"),
        autoescape=True,
        keep_trailing_newline=True,
    )
    charts = [c.as_dict() for c in all_charts(data, int(data.report_window["batch_job_hours"]))]
    html = env.get_template("index.html.j2").render(
        data=data,
        tiles=kpi_tiles(data),
        charts={c["id"]: c for c in charts},
        charts_json=_json_for_script(charts),
        has_csv=annual_csv is not None and annual_csv.exists(),
        has_powerbi=(out_dir / "downloads" / "powerbi" / "manifest.json").exists(),
        csv_name=annual_csv.name if annual_csv is not None else "",
    )
    assets = out_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(html, encoding="utf-8", newline="\n")
    (assets / "plotly.min.js").write_text(get_plotlyjs(), encoding="utf-8", newline="\n")
    static = resources.files("gridwatch.dashboard") / "static"
    for name in ("style.css", "app.js"):
        (assets / name).write_text(
            static.joinpath(name).read_text(encoding="utf-8"), encoding="utf-8", newline="\n"
        )
    if annual_csv is not None and annual_csv.exists():
        (out_dir / "data").mkdir(exist_ok=True)
        shutil.copyfile(annual_csv, out_dir / "data" / annual_csv.name)
    (out_dir / ".nojekyll").write_text("", encoding="utf-8", newline="\n")
    return out_dir / "index.html"


def build_site(
    warehouse: Path, outputs_dir: Path, out_dir: Path, annual_csv: Path | None = None
) -> Path:
    return render(collect(warehouse, outputs_dir), out_dir, annual_csv)

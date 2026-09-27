"""Plotly chart specifications for the dashboard.

Specs are plain dictionaries (Plotly's JSON schema). Colours are not baked in: each trace
carries ``meta.role`` (series-1, series-2, muted, band-1, heat) and the page script paints
it from CSS custom properties, so light and dark themes use their own validated steps.

A chart with no rows gets no spec, so the page shows an explicit empty state instead of
blank axes (for example the regional chart when regional data starts after the report
window).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from gridwatch.dashboard.data import DashboardData

Spec = dict[str, Any]

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
SEASONS = ["Winter", "Spring", "Summer", "Autumn"]
METHOD_LABELS = {
    "model": "gridwatch model",
    "naive_yesterday": "Same half-hour, last known day",
    "naive_last_week": "Same half-hour, last week",
    "api_forecast": "API retained forecast (short lead)",
}


@dataclass
class Chart:
    chart_id: str
    title: str
    subtitle: str
    spec: Spec | None
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.chart_id,
            "title": self.title,
            "subtitle": self.subtitle,
            "spec": self.spec,
            "columns": self.columns,
            "rows": self.rows,
            "note": self.note,
        }


def _empty(chart_id: str, title: str, subtitle: str) -> Chart:
    """A chart with nothing to draw: the page shows its empty state and no table."""
    return Chart(chart_id, title, subtitle, None)


def _r(value: Any, digits: int = 1) -> Any:
    return round(value, digits) if isinstance(value, float) else value


def _layout(y_title: str, x_title: str = "", **extra: Any) -> Spec:
    layout: Spec = {
        "margin": {"l": 56, "r": 16, "t": 8, "b": 48},
        "hovermode": "x unified",
        "showlegend": True,
        "legend": {"orientation": "h", "y": -0.22, "x": 0},
        "yaxis": {"title": {"text": y_title}, "rangemode": "tozero", "zeroline": False},
        "xaxis": {"title": {"text": x_title}},
    }
    layout.update(extra)
    return layout


def _line(x: Sequence[Any], y: Sequence[Any], name: str, role: str, **extra: Any) -> Spec:
    trace: Spec = {
        "type": "scatter",
        "mode": "lines",
        "x": list(x),
        "y": list(y),
        "name": name,
        "line": {"width": 2},
        "meta": {"role": role},
        "hovertemplate": "%{y:.0f}",
    }
    trace.update(extra)
    return trace


def _band(
    x: Sequence[Any], low: Sequence[Any], high: Sequence[Any], name: str, role: str
) -> list[Spec]:
    """A shaded range between two lines (drawn as two traces, the second filled)."""
    return [
        {
            "type": "scatter",
            "mode": "lines",
            "x": list(x),
            "y": list(low),
            "line": {"width": 0},
            "showlegend": False,
            "hoverinfo": "skip",
            "meta": {"role": role, "band": True},
        },
        {
            "type": "scatter",
            "mode": "lines",
            "x": list(x),
            "y": list(high),
            "fill": "tonexty",
            "line": {"width": 0},
            "name": name,
            "hoverinfo": "skip",
            "meta": {"role": role, "band": True},
        },
    ]


def next_48_hours(data: DashboardData) -> Chart:
    title = "The last two days and the next two"
    if not (data.recent or data.model_forecast or data.api_snapshot):
        return _empty("next48", title, "No recent GB data.")
    recent_x = [r["period_start_utc"] for r in data.recent]
    traces: list[Spec] = [
        _line(recent_x, [r["actual_gco2_kwh"] for r in data.recent], "Actual", "series-1"),
    ]
    if data.model_forecast:
        fx = [r["period_start_utc"] for r in data.model_forecast]
        traces += _band(
            fx,
            [r["p10_gco2_kwh"] for r in data.model_forecast],
            [r["p90_gco2_kwh"] for r in data.model_forecast],
            "gridwatch 10-90% range",
            "series-2",
        )
        traces.append(
            _line(
                fx,
                [r["forecast_gco2_kwh"] for r in data.model_forecast],
                "gridwatch forecast",
                "series-2",
            )
        )
    if data.api_snapshot:
        traces.append(
            _line(
                [r["period_start_utc"] for r in data.api_snapshot],
                [r["forecast_gco2_kwh"] for r in data.api_snapshot],
                "API forecast",
                "series-3",
            )
        )
    rows: list[list[Any]] = []
    model = {r["period_start_utc"]: r for r in data.model_forecast}
    api = {r["period_start_utc"]: r["forecast_gco2_kwh"] for r in data.api_snapshot}
    for r in data.recent:
        rows.append([r["period_start_utc"], r["actual_gco2_kwh"], None, None])
    for ts in sorted(set(model) | set(api)):
        m = model.get(ts)
        rows.append([ts, None, _r(m["forecast_gco2_kwh"]) if m else None, api.get(ts)])
    issued = (
        data.model_forecast[0]["issued_at_utc"].replace("T", " ") + " UTC"
        if data.model_forecast
        else "n/a"
    )
    layout = _layout("gCO2/kWh", "UTC")
    # Legend above the plot, so it is in view with the top of the chart (and the README
    # screenshot) rather than below the axis.
    layout["legend"] = {"orientation": "h", "y": 1.02, "yanchor": "bottom", "x": 0}
    layout["margin"] = {"l": 56, "r": 16, "t": 32, "b": 48}
    return Chart(
        "next48",
        title,
        f"GB national intensity, gCO2/kWh, UTC. Model forecast issued {issued}.",
        {"data": traces, "layout": layout},
        ["Half-hour (UTC)", "Actual", "gridwatch forecast", "API forecast"],
        rows,
    )


def weekly_heatmap(data: DashboardData) -> Chart:
    title = "An average week"
    if not data.weekly_profile:
        return _empty("week", title, "No actual values in the report window.")
    slots = sorted({(r["time_key"], r["start_time_label"]) for r in data.weekly_profile})
    labels = [label for _, label in slots]
    grid: dict[tuple[int, int], Any] = {
        (r["iso_day_of_week"], r["time_key"]): r["mean_actual_gco2_kwh"]
        for r in data.weekly_profile
    }
    z = [[_r(grid.get((day, key))) for key, _ in slots] for day in range(1, 8)]
    trace: Spec = {
        "type": "heatmap",
        "x": labels,
        "y": DAY_NAMES,
        "z": z,
        "xgap": 2,
        "ygap": 2,
        "meta": {"role": "heat"},
        "colorbar": {"title": {"text": "gCO2/kWh"}, "thickness": 12},
        "hovertemplate": "%{y} %{x}<br><b>%{z:.0f}</b> gCO2/kWh<extra></extra>",
    }
    layout = _layout("", "UK local time", hovermode="closest", showlegend=False)
    layout["yaxis"] = {"autorange": "reversed"}
    layout["xaxis"]["dtick"] = 4
    rows = [[DAY_NAMES[d - 1], *z[d - 1]] for d in range(1, 8)]
    return Chart(
        "week",
        title,
        "Mean actual intensity by UK local day and half-hour over the report window, gCO2/kWh.",
        {"data": [trace], "layout": layout},
        ["Day", *labels],
        rows,
    )


def start_slots(data: DashboardData, hours: int) -> Chart:
    title = f"When to start a {hours}-hour flexible job"
    if not data.start_slots:
        return _empty("slots", title, "No complete job windows in the report window.")
    x = [r["start_time_label"] for r in data.start_slots]
    traces = [
        _line(
            x,
            [r["working_day_mean_job_gco2_kwh"] for r in data.start_slots],
            "Working days",
            "series-1",
        ),
        _line(
            x,
            [r["non_working_day_mean_job_gco2_kwh"] for r in data.start_slots],
            "Weekends and bank holidays",
            "series-2",
        ),
    ]
    layout = _layout("Mean gCO2/kWh during the job", "Start time (UK local)")
    layout["xaxis"]["dtick"] = 4
    rows = [
        [
            r["start_time_label"],
            _r(r["mean_job_gco2_kwh"]),
            _r(r["working_day_mean_job_gco2_kwh"]),
            _r(r["non_working_day_mean_job_gco2_kwh"]),
            r["rank_lowest"],
        ]
        for r in data.start_slots
    ]
    return Chart(
        "slots",
        title,
        "Mean intensity the job would have seen, by start time, over the report window.",
        {"data": traces, "layout": layout},
        ["Start", "All days", "Working days", "Weekends and holidays", "Rank (1 = lowest)"],
        rows,
    )


def strategies(data: DashboardData) -> Chart:
    title = "Scheduling rules compared"
    if not data.strategies:
        return _empty("strategies", title, "No day in the report window has every rule scored.")
    ordered = list(reversed(data.strategies))
    trace: Spec = {
        "type": "bar",
        "orientation": "h",
        "x": [_r(r["mean_job_gco2_kwh"]) for r in ordered],
        "y": [r["strategy_label"] for r in ordered],
        "meta": {"role": "series-1"},
        "width": 0.55,
        "text": [f"{r['mean_job_gco2_kwh']:.0f}" for r in ordered],
        "textposition": "outside",
        "cliponaxis": False,
        "hovertemplate": "%{y}<br><b>%{x:.1f}</b> gCO2/kWh<extra></extra>",
    }
    layout = _layout("", "Mean gCO2/kWh during the job", hovermode="closest", showlegend=False)
    layout["margin"] = {"l": 250, "r": 40, "t": 8, "b": 48}
    layout["yaxis"] = {"automargin": True}
    rows = [
        [
            r["strategy_label"],
            r["days"],
            _r(r["mean_job_gco2_kwh"]),
            _r(r["saving_vs_0900_pct"]),
            _r(r["saving_vs_1700_pct"]),
            _r(r["kg_co2_per_run"]),
        ]
        for r in data.strategies
    ]
    return Chart(
        "strategies",
        title,
        "One run a day; the same days for every rule. Lower is better.",
        {"data": [trace], "layout": layout},
        [
            "Rule",
            "Days",
            "Mean gCO2/kWh",
            "Saving vs 09:00 %",
            "Saving vs 17:00 %",
            "kg CO2 per run",
        ],
        rows,
        note="The API-forecast rule uses the forecast the API keeps for past half-hours, "
        "which was issued shortly before each half-hour, so it flatters that rule.",
    )


def seasonal_profile(data: DashboardData) -> Chart:
    title = "The average day in each season"
    if not data.seasonal_profile:
        return _empty("seasons", title, "No actual values in the report window.")
    traces = []
    for index, season in enumerate(SEASONS, start=1):
        part = [r for r in data.seasonal_profile if r["season"] == season]
        traces.append(
            _line(
                [r["start_time_label"] for r in part],
                [r["mean_actual_gco2_kwh"] for r in part],
                season,
                f"series-{index}",
            )
        )
    layout = _layout("gCO2/kWh", "UK local time")
    layout["xaxis"]["dtick"] = 4
    by_slot: dict[str, dict[str, Any]] = {}
    for r in data.seasonal_profile:
        by_slot.setdefault(r["start_time_label"], {})[r["season"]] = _r(r["mean_actual_gco2_kwh"])
    rows = [[slot, *(by_slot[slot].get(s) for s in SEASONS)] for slot in sorted(by_slot)]
    return Chart(
        "seasons",
        title,
        "Mean actual intensity by UK local half-hour over the report window, gCO2/kWh.",
        {"data": traces, "layout": layout},
        ["Half-hour", *SEASONS],
        rows,
    )


def monthly_trend(data: DashboardData) -> Chart:
    title = "GB intensity month by month"
    if not data.monthly:
        return _empty("monthly", title, "No monthly values yet.")
    x = [r["month_start"] for r in data.monthly]
    traces = _band(
        x,
        [r["p10_actual_gco2_kwh"] for r in data.monthly],
        [r["p90_actual_gco2_kwh"] for r in data.monthly],
        "10th to 90th percentile",
        "series-1",
    )
    traces.append(
        _line(x, [r["mean_actual_gco2_kwh"] for r in data.monthly], "Monthly mean", "series-1")
    )
    rows = [
        [
            r["year_month"],
            _r(r["mean_actual_gco2_kwh"]),
            _r(r["p10_actual_gco2_kwh"]),
            _r(r["p90_actual_gco2_kwh"]),
            _r(r["mean_low_carbon_pct"]),
            _r(r["coverage_pct"]),
        ]
        for r in data.monthly
    ]
    return Chart(
        "monthly",
        title,
        "Monthly mean and spread of half-hourly actual intensity, gCO2/kWh.",
        {"data": traces, "layout": _layout("gCO2/kWh")},
        ["Month", "Mean", "P10", "P90", "Low-carbon share %", "Coverage %"],
        rows,
    )


def regions(data: DashboardData) -> Chart:
    title = "Regions of GB"
    if not data.regions:
        return _empty(
            "regions",
            title,
            "No regional data in the report window (regional ingestion may start later).",
        )
    ordered = list(reversed(data.regions))
    trace: Spec = {
        "type": "bar",
        "orientation": "h",
        "x": [_r(r["mean_forecast_gco2_kwh"]) for r in ordered],
        "y": [r["region_short_name"] for r in ordered],
        "meta": {"role": "series-1"},
        "width": 0.6,
        "hovertemplate": "%{y}<br><b>%{x:.0f}</b> gCO2/kWh<extra></extra>",
    }
    layout = _layout("", "Mean forecast gCO2/kWh", hovermode="closest", showlegend=False)
    layout["margin"] = {"l": 170, "r": 16, "t": 8, "b": 48}
    layout["yaxis"] = {"automargin": True}
    rows = [
        [
            r["region_short_name"],
            r["nation"],
            _r(r["mean_forecast_gco2_kwh"]),
            _r(r["winter_mean_gco2_kwh"]),
            _r(r["summer_mean_gco2_kwh"]),
            _r(r["mean_wind_pct"]),
            _r(r["mean_low_carbon_pct"]),
        ]
        for r in data.regions
    ]
    return Chart(
        "regions",
        title,
        "Mean regional forecast intensity over the report window. The API has no regional "
        "actuals, so these are modelled values.",
        {"data": [trace], "layout": layout},
        ["Region", "Nation", "Mean", "Winter", "Summer", "Wind %", "Low-carbon %"],
        rows,
    )


HIGHLIGHT = {"ARE": "series-1", "GCC": "series-2", "GBR": "series-3", "EU27": "series-4"}


def country_trend(data: DashboardData) -> Chart:
    title = "UAE and GCC against the UK and EU"
    if not data.country_trend:
        return _empty("countries", title, "No Ember data yet.")
    traces: list[Spec] = []
    codes = sorted({r["country_code"] for r in data.country_trend})
    names = {r["country_code"]: r["country_name"] for r in data.country_trend}
    # Other GCC members and the world benchmark recede; the four areas in the question lead.
    order = [c for c in codes if c not in HIGHLIGHT] + [c for c in HIGHLIGHT if c in codes]
    for code in order:
        part = [r for r in data.country_trend if r["country_code"] == code]
        role = HIGHLIGHT.get(code, "muted")
        traces.append(
            _line(
                [r["year"] for r in part],
                [r["intensity_gco2e_kwh"] for r in part],
                names[code],
                role,
                line={"width": 2 if code in HIGHLIGHT else 1},
            )
        )
    years = sorted({r["year"] for r in data.country_trend})
    grid = {
        (r["country_code"], r["year"]): _r(r["intensity_gco2e_kwh"], 0) for r in data.country_trend
    }
    rows = [[names[c], *(grid.get((c, y)) for y in years)] for c in order[::-1]]
    return Chart(
        "countries",
        title,
        "Annual lifecycle emissions intensity, gCO2e/kWh (Ember). Grey lines: other GCC "
        "members and the world average.",
        {"data": traces, "layout": _layout("gCO2e/kWh")},
        ["Area", *[str(y) for y in years]],
        rows,
    )


def backtest(data: DashboardData) -> Chart:
    if not data.backtest_by_horizon:
        return Chart("backtest", "Forecast accuracy by lead time", "Backtest not run yet.", None)
    x = [r["hours_ahead"] for r in data.backtest_by_horizon]
    roles = {
        "model": "series-2",
        "naive_yesterday": "series-1",
        "naive_last_week": "muted",
        "api_forecast": "series-3",
    }
    traces = [
        _line(
            x,
            [r[m] for r in data.backtest_by_horizon],
            METHOD_LABELS[m],
            roles[m],
            hovertemplate="%{y:.1f}",
        )
        for m in ("naive_last_week", "naive_yesterday", "model", "api_forecast")
    ]
    rows = [
        [
            r["horizon_band"],
            METHOD_LABELS.get(r["method"], r["method"]),
            r["n"],
            _r(r["mae"], 2),
            _r(r["rmse"], 2),
            _r(r["mape"], 2),
            _r(r["bias"], 2),
        ]
        for r in data.backtest_metrics
    ]
    meta = data.backtest_meta
    return Chart(
        "backtest",
        "Forecast accuracy by lead time",
        f"Mean absolute error, gCO2/kWh, over {meta.get('origins')} daily forecasts from "
        f"{meta.get('first_origin_utc')} to {meta.get('last_origin_utc')}.",
        {"data": traces, "layout": _layout("MAE, gCO2/kWh", "Hours ahead")},
        ["Horizon", "Method", "Pairs", "MAE", "RMSE", "MAPE %", "Bias"],
        rows,
        note="The API's retained forecast was issued shortly before each half-hour, not a "
        "day ahead, so it is not a like-for-like competitor at 24-48 hours.",
    )


def all_charts(data: DashboardData, batch_hours: int) -> list[Chart]:
    return [
        next_48_hours(data),
        start_slots(data, batch_hours),
        weekly_heatmap(data),
        strategies(data),
        seasonal_profile(data),
        monthly_trend(data),
        regions(data),
        country_trend(data),
        backtest(data),
    ]

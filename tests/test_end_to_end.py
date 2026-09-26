"""Replay the recorded cassette through the whole pipeline, including the dbt build.

No network: HTTP comes from tests/fixtures/cassette and dbt reads local Parquet.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import duckdb
import pytest

from gridwatch.cli import main
from tests.helpers import CASSETTE, read_fixture_env

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def pipeline(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("e2e")
    env = read_fixture_env()
    patch = pytest.MonkeyPatch()
    patch.setenv("GRIDWATCH_DATA_DIR", str(root / "data"))
    patch.setenv("DBT_SEND_ANONYMOUS_USAGE_STATS", "false")
    for key, value in env.items():
        if key.startswith("GRIDWATCH_"):
            patch.setenv(key, value)
    as_of = env["FIXTURE_AS_OF"]
    try:
        assert main(["ingest", "--replay", str(CASSETTE), "--as-of", as_of]) == 0
        assert main(["transform", "--as-of", as_of]) == 0
        assert main(["forecast", "--train-days", "30"]) == 0
        assert (
            main(["backtest", "--train-days", "30", "--test-days", "7", "--retrain-every", "7"])
            == 0
        )
        assert main(["report", "--out", str(root / "report.md")]) == 0
        assert (
            main(
                [
                    "export",
                    "--annual-csv",
                    str(root / "annual.csv"),
                    "--powerbi-dir",
                    str(root / "powerbi"),
                    "--half-hourly-days",
                    "14",
                ]
            )
            == 0
        )
        assert main(["docs", "--out", str(root / "site" / "dbt")]) == 0
        assert (
            main(["site", "--out", str(root / "site"), "--annual-csv", str(root / "annual.csv")])
            == 0
        )
    finally:
        patch.undo()
    return root


def test_marts_are_built(pipeline: Path) -> None:
    warehouse = pipeline / "data" / "warehouse" / "gridwatch.duckdb"
    with duckdb.connect(str(warehouse), read_only=True) as con:
        national = con.sql(
            "select count(*), count(*) filter (where is_actual_implausible) "
            "from marts.fct_national_intensity"
        ).fetchone()
        assert national is not None
        assert national[0] == 59 * 48
        # the fixture window holds one real upstream actual of 0 gCO2/kWh, which is removed
        assert national[1] == 1
        savings = con.sql("select count(*) from reporting.rpt_batch_job_savings").fetchone()
        assert savings == (7,)
        countries = con.sql(
            "select count(*) from reporting.rpt_country_intensity_comparison"
        ).fetchone()
        assert countries == (10,)  # nine areas plus the GCC aggregate


def test_report_has_every_section(pipeline: Path) -> None:
    report = (pipeline / "report.md").read_text(encoding="utf-8")
    for heading in ("## (a)", "## (b)", "## (c)", "## (d)", "## Forecast backtest"):
        assert heading in report


def test_backtest_outputs(pipeline: Path) -> None:
    summary = json.loads(
        (pipeline / "data" / "outputs" / "backtest_summary.json").read_text(encoding="utf-8")
    )
    assert summary["origins"] == 7
    methods = {m["method"] for m in summary["metrics"]}
    assert methods == {"model", "naive_yesterday", "naive_last_week", "api_forecast"}


def test_exports(pipeline: Path) -> None:
    with (pipeline / "annual.csv").open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    codes = {r["country_code"] for r in rows}
    assert codes == {"ARE", "SAU", "QAT", "KWT", "BHR", "OMN", "GBR", "EU27"}
    assert all(r["licence"] == "CC BY 4.0" for r in rows)
    manifest = json.loads((pipeline / "powerbi" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["tables"]["fct_national_intensity_half_hourly.csv"] == 14 * 48 + 1


def test_site(pipeline: Path) -> None:
    html = (pipeline / "site" / "index.html").read_text(encoding="utf-8")
    assert "How clean is the electricity" in html
    assert (pipeline / "site" / "data" / "annual.csv").exists()
    # the API forecast snapshots are published with the page, as their durable copy
    snapshots = pipeline / "site" / "data" / "forecast_snapshots" / "manifest.json"
    assert json.loads(snapshots.read_text(encoding="utf-8"))["rows"] == 97
    assert 'href="data/forecast_snapshots/manifest.json"' in html
    # dbt docs with the exposures, linked from the footer
    docs = (pipeline / "site" / "dbt" / "index.html").read_text(encoding="utf-8")
    assert "exposure.gridwatch.gridwatch_dashboard" in docs
    assert 'href="dbt/index.html"' in html

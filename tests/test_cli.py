from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from gridwatch.cli import build_parser, main
from gridwatch.config import Settings
from gridwatch.forecast.model import ModelConfig
from gridwatch.ingest.http import HttpFetcher
from gridwatch.transform import TransformError, check_inputs


def test_parser_knows_every_command() -> None:
    parser = build_parser()
    for command in (
        "ingest",
        "transform",
        "forecast",
        "backtest",
        "report",
        "export",
        "site",
        "run",
    ):
        args = parser.parse_args([command])
        assert args.command == command
    args = parser.parse_args(["restore-snapshots", "https://example.test/snapshots/"])
    assert args.url == "https://example.test/snapshots/"


def test_ingest_rejects_unknown_source() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["ingest", "--source", "weather"])


def test_config_error_exits_with_status_2(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("GRIDWATCH_REFETCH_DAYS", "lots")
    assert main(["transform"]) == 2
    assert "GRIDWATCH_REFETCH_DAYS" in caplog.text


def test_transform_without_data_explains_what_to_do(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("GRIDWATCH_DATA_DIR", str(tmp_path))
    assert main(["transform"]) == 2
    assert "Run `gridwatch ingest` first" in caplog.text


def test_check_inputs(tmp_path: Path) -> None:
    with pytest.raises(TransformError, match="raw data not found"):
        check_inputs(Settings.from_env({"GRIDWATCH_DATA_DIR": str(tmp_path)}))


def test_forecast_without_warehouse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("GRIDWATCH_DATA_DIR", str(tmp_path))
    assert main(["forecast"]) == 2
    assert "run `gridwatch transform` first" in caplog.text


def test_forecast_output_is_plain_ascii(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import polars as pl

    from gridwatch import cli

    frame = pl.DataFrame(
        {
            "period_start_utc": [datetime(2026, 1, 1, 0, 0)],
            "forecast_gco2_kwh": [123.4],
            "p10_gco2_kwh": [100.0],
            "p90_gco2_kwh": [150.0],
        }
    )
    monkeypatch.setattr(cli, "run_forecast", lambda settings, config: frame)
    settings = Settings.from_env({"GRIDWATCH_DATA_DIR": str(tmp_path)})
    args = build_parser().parse_args(["forecast"])
    assert cli.cmd_forecast(args, settings) == 0
    out = capsys.readouterr().out
    assert "2026-01-01 00:00 UTC   123.4 gCO2/kWh" in out
    out.encode("ascii")  # a cp1252 console must be able to print it


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["forecast", "--train-days", "5"], "5 is not between 14 and 3650"),
        (["backtest", "--test-days", "0"], "0 is not between 1 and 3650"),
        (["backtest", "--origin-hour", "24"], "24 is not between 0 and 23"),
        (["backtest", "--until", "yesterday"], "'yesterday' is not an ISO 8601 time"),
        (["transform", "--report-start", "2025-13-01"], "is not a date such as 2025-09-01"),
        (["export", "--half-hourly-days", "lots"], "'lots' is not a whole number"),
    ],
)
def test_invalid_arguments_are_rejected_with_a_message(
    argv: list[str], message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(argv)
    assert excinfo.value.code == 2
    assert message in capsys.readouterr().err


def test_report_window_must_not_be_reversed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("GRIDWATCH_DATA_DIR", str(tmp_path))
    argv = ["transform", "--report-start", "2026-01-01", "--report-end", "2025-12-31"]
    assert main(argv) == 2
    assert "--report-start 2026-01-01 is after --report-end 2025-12-31" in caplog.text


@pytest.mark.parametrize("command", ["report", "export", "site"])
def test_commands_that_read_the_warehouse_explain_what_to_do(
    command: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("GRIDWATCH_DATA_DIR", str(tmp_path))
    assert main([command, *(["--out", str(tmp_path / "x")] if command != "export" else [])]) == 2
    assert "run `gridwatch transform` first" in caplog.text


def test_unexpected_database_error_is_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import duckdb

    import gridwatch.report

    def locked(*args: object) -> Path:
        raise duckdb.IOException("Could not set lock on file")

    settings = Settings.from_env({"GRIDWATCH_DATA_DIR": str(tmp_path)})
    settings.warehouse_path.parent.mkdir(parents=True)
    settings.warehouse_path.touch()
    monkeypatch.setenv("GRIDWATCH_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(gridwatch.report, "write_report", locked)
    assert main(["report", "--out", str(tmp_path / "report.md")]) == 2
    assert "IOException: Could not set lock on file" in caplog.text
    assert "Traceback" not in caplog.text


def test_run_passes_model_settings_to_forecast_and_backtest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gridwatch import cli

    seen: dict[str, object] = {}

    def record(name: str) -> Callable[[argparse.Namespace, Settings], int]:
        def handler(args: argparse.Namespace, settings: Settings) -> int:
            if name in ("forecast", "backtest"):
                seen[name] = cli._model_config(args)
            return 0

        return handler

    for name in ("ingest", "transform", "forecast", "backtest", "export", "site"):
        monkeypatch.setattr(cli, f"cmd_{name}", record(name))
    monkeypatch.setenv("GRIDWATCH_DATA_DIR", str(tmp_path))
    assert main(["run", "--train-days", "60", "--calibration-days", "0"]) == 0
    for name in ("forecast", "backtest"):
        config = seen[name]
        assert isinstance(config, ModelConfig)
        assert config.train_days == 60
        assert config.calibration_days == 0


def test_restore_snapshots_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    fetcher_factory: Callable[..., HttpFetcher],
) -> None:
    from gridwatch import cli

    monkeypatch.setenv("GRIDWATCH_DATA_DIR", str(tmp_path))
    status = {"code": 404}
    transport = httpx.MockTransport(lambda request: httpx.Response(status["code"]))
    monkeypatch.setattr(cli, "_fetcher", lambda *args, **kwargs: fetcher_factory(transport))
    assert main(["restore-snapshots", "https://example.test/data/forecast_snapshots/"]) == 0
    assert capsys.readouterr().out.startswith("no-mirror: 0 file(s)")
    status["code"] = 403
    assert main(["restore-snapshots", "https://example.test/data/forecast_snapshots/"]) == 2
    assert "could not read the snapshot manifest" in caplog.text

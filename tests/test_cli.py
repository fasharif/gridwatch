from __future__ import annotations

from pathlib import Path

import pytest

from gridwatch.cli import build_parser, main
from gridwatch.config import Settings
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

"""Command-line interface: ``gridwatch <command>``."""

from __future__ import annotations

import argparse
import logging
import random
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import httpx

from gridwatch.config import ConfigError, Settings
from gridwatch.forecast.backtest import BacktestConfig
from gridwatch.forecast.data import SeriesError
from gridwatch.forecast.model import ModelConfig, NotEnoughHistoryError
from gridwatch.forecast.service import FORECAST_FILE, run_backtest_job, run_forecast
from gridwatch.ingest.cassette import CassetteMissError, RecordingTransport, ReplayTransport
from gridwatch.ingest.http import HttpFetcher, RateLimiter, RetryPolicy, build_client
from gridwatch.ingest.pipeline import (
    ALL_SOURCES,
    IngestError,
    ensure_empty_datasets,
    run_ingest,
)
from gridwatch.transform import TransformError, run_dbt

REPO_ROOT = Path(__file__).resolve().parents[2]

log = logging.getLogger("gridwatch")


def _parse_as_of(value: str | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--as-of {value!r} is not an ISO 8601 time") from exc
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _fetcher(
    settings: Settings, transport: httpx.BaseTransport | None, replaying: bool
) -> HttpFetcher:
    client = build_client(settings.user_agent, settings.timeout_s, transport)
    interval = 0.0 if replaying else settings.min_request_interval_s
    return HttpFetcher(
        client,
        RateLimiter(interval),
        RetryPolicy(max_attempts=settings.max_attempts),
        rng=random.Random(),
    )


def cmd_ingest(args: argparse.Namespace, settings: Settings) -> int:
    now = _parse_as_of(args.as_of)
    transport: httpx.BaseTransport | None = None
    if args.replay:
        transport = ReplayTransport(Path(args.replay))
    elif args.record:
        transport = RecordingTransport(Path(args.record))
    fetcher = _fetcher(settings, transport, replaying=bool(args.replay))
    sources = args.source or list(ALL_SOURCES)
    report = run_ingest(settings, fetcher, now, sources, repair_gaps=args.repair_gaps)
    ensure_empty_datasets(settings.raw_dir)
    print(report.to_json())
    log.info("ingest finished with %d HTTP request(s)", fetcher.request_count)
    return 0


def _dbt_vars(args: argparse.Namespace) -> dict[str, object]:
    variables: dict[str, object] = {}
    if getattr(args, "as_of", None):
        variables["as_of"] = _parse_as_of(args.as_of).strftime("%Y-%m-%d %H:%M:%S")
    if getattr(args, "report_start", None):
        variables["report_start_date"] = args.report_start
    if getattr(args, "report_end", None):
        variables["report_end_date"] = args.report_end
    return variables


def cmd_transform(args: argparse.Namespace, settings: Settings) -> int:
    ensure_empty_datasets(settings.raw_dir)
    result = run_dbt(settings, ("build",), _dbt_vars(args))
    for warning in result.warnings:
        log.warning("dbt warning: %s (%s)", warning["name"], warning["message"])
    if not result.success:
        for failure in result.failures:
            log.error("dbt failure: %s (%s)", failure["name"], failure["message"])
        raise TransformError(f"dbt build failed ({len(result.failures)} node(s))")
    log.info("dbt build passed: %d nodes, %d warning(s)", len(result.results), len(result.warnings))
    return 0


def _model_config(args: argparse.Namespace) -> ModelConfig:
    # Hold out up to 56 days (and at most a fifth of the window) to calibrate the interval.
    return ModelConfig(
        train_days=args.train_days,
        calibration_days=min(
            getattr(args, "calibration_days", ModelConfig.calibration_days), args.train_days // 5
        ),
    )


def cmd_forecast(args: argparse.Namespace, settings: Settings) -> int:
    frame = run_forecast(settings, _model_config(args))
    # Plain ASCII output: a Windows console may not be able to encode table borders.
    for row in frame.head(4).iter_rows(named=True):
        print(
            f"{row['period_start_utc']:%Y-%m-%d %H:%M} UTC  {row['forecast_gco2_kwh']:6.1f} "
            f"gCO2/kWh  (10-90%: {row['p10_gco2_kwh']:.0f} to {row['p90_gco2_kwh']:.0f})"
        )
    print(f"... {frame.height} half-hours in {settings.outputs_dir / FORECAST_FILE}")
    return 0


def cmd_backtest(args: argparse.Namespace, settings: Settings) -> int:
    config = BacktestConfig(
        test_days=args.test_days,
        retrain_every_days=args.retrain_every,
        origin_hour_utc=args.origin_hour,
        model=_model_config(args),
    )
    until = _parse_as_of(args.until) if getattr(args, "until", None) else None
    summary = run_backtest_job(settings, config, until)
    for row in summary["metrics"]:  # type: ignore[attr-defined]
        log.info(
            "%-8s %-16s MAE %6.2f  RMSE %6.2f  MAPE %5.1f%%",
            row["horizon_band"],
            row["method"],
            row["mae"],
            row["rmse"],
            row["mape"],
        )
    return 0


def cmd_report(args: argparse.Namespace, settings: Settings) -> int:
    from gridwatch.report import write_report

    path = write_report(settings.warehouse_path, settings.outputs_dir, Path(args.out))
    log.info("report tables written to %s", path)
    return 0


def cmd_export(args: argparse.Namespace, settings: Settings) -> int:
    from gridwatch.export import export_annual_intensity, export_powerbi

    rows = export_annual_intensity(settings.warehouse_path, Path(args.annual_csv))
    log.info("%d rows written to %s", rows, args.annual_csv)
    if args.powerbi_dir:
        result = export_powerbi(
            settings.warehouse_path, Path(args.powerbi_dir), args.half_hourly_days
        )
        for path, count in result.files.items():
            log.info("%7d rows -> %s", count, path)
    return 0


def cmd_site(args: argparse.Namespace, settings: Settings) -> int:
    from gridwatch.dashboard.build import build_site

    csv = Path(args.annual_csv) if args.annual_csv else None
    index = build_site(settings.warehouse_path, settings.outputs_dir, Path(args.out), csv)
    log.info("dashboard written to %s", index)
    return 0


def cmd_run(args: argparse.Namespace, settings: Settings) -> int:
    """The daily pipeline: ingest, transform, forecast, backtest, export and site."""
    steps: list[tuple[str, argparse.Namespace]] = [
        (
            "ingest",
            argparse.Namespace(
                as_of=args.as_of, replay=args.replay, record=None, source=None, repair_gaps=False
            ),
        ),
        ("transform", argparse.Namespace(as_of=args.as_of, report_start=None, report_end=None)),
        ("forecast", argparse.Namespace(train_days=args.train_days)),
    ]
    if not args.skip_backtest:
        steps.append(
            (
                "backtest",
                argparse.Namespace(
                    train_days=args.train_days,
                    test_days=args.test_days,
                    retrain_every=BacktestConfig.retrain_every_days,
                    origin_hour=BacktestConfig.origin_hour_utc,
                ),
            )
        )
    steps += [
        (
            "export",
            argparse.Namespace(
                annual_csv=args.annual_csv, powerbi_dir=args.powerbi_dir, half_hourly_days=365
            ),
        ),
        ("site", argparse.Namespace(out=args.site_dir, annual_csv=args.annual_csv)),
    ]
    handlers = {
        "ingest": cmd_ingest,
        "transform": cmd_transform,
        "forecast": cmd_forecast,
        "backtest": cmd_backtest,
        "export": cmd_export,
        "site": cmd_site,
    }
    for name, namespace in steps:
        log.info("== %s ==", name)
        handlers[name](namespace, settings)
    return 0


def _add_model_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--train-days",
        type=int,
        default=ModelConfig.train_days,
        help="days of history each model is trained on (default: %(default)s)",
    )
    parser.add_argument(
        "--calibration-days",
        type=int,
        default=ModelConfig.calibration_days,
        help="recent days held out to calibrate the interval; 0 turns calibration off "
        "(default: %(default)s, capped at a fifth of --train-days)",
    )


def _add_window_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--report-start", help="first UK local date of the report window")
    parser.add_argument("--report-end", help="last UK local date of the report window")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gridwatch",
        description="Electricity carbon analytics: ingest, transform, forecast and report.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="fetch new data into raw Parquet (incremental)")
    ingest.add_argument(
        "--source",
        action="append",
        choices=ALL_SOURCES,
        help="source to ingest (repeatable; default: all)",
    )
    ingest.add_argument("--as-of", help="treat this UTC time as 'now' (ISO 8601)")
    ingest.add_argument(
        "--repair-gaps",
        action="store_true",
        help="also re-request every missing half-hour inside the stored history",
    )
    group = ingest.add_mutually_exclusive_group()
    group.add_argument("--replay", metavar="DIR", help="serve HTTP from a recorded cassette")
    group.add_argument("--record", metavar="DIR", help="record HTTP responses into DIR")
    ingest.set_defaults(func=cmd_ingest)

    transform = sub.add_parser("transform", help="build and test the dbt project")
    transform.add_argument("--as-of", help="UTC time used by the freshness test")
    _add_window_args(transform)
    transform.set_defaults(func=cmd_transform)

    forecast = sub.add_parser("forecast", help="forecast the next 48 hours of GB intensity")
    _add_model_args(forecast)
    forecast.set_defaults(func=cmd_forecast)

    backtest = sub.add_parser("backtest", help="rolling-origin backtest against baselines")
    _add_model_args(backtest)
    backtest.add_argument(
        "--test-days",
        type=int,
        default=BacktestConfig.test_days,
        help="daily forecast origins to test (default: %(default)s)",
    )
    backtest.add_argument(
        "--retrain-every",
        type=int,
        default=BacktestConfig.retrain_every_days,
        help="retrain the model every N origins (default: %(default)s)",
    )
    backtest.add_argument(
        "--until",
        help="use only data before this UTC time, e.g. to validate settings on a period "
        "before the test year (outputs get a suffix)",
    )
    backtest.add_argument(
        "--origin-hour",
        type=int,
        default=BacktestConfig.origin_hour_utc,
        help="UTC hour at which each forecast is issued (default: %(default)s)",
    )
    backtest.set_defaults(func=cmd_backtest)

    report = sub.add_parser("report", help="write the Markdown tables behind docs/findings.md")
    report.add_argument("--out", default=str(REPO_ROOT / "docs" / "generated" / "report.md"))
    report.set_defaults(func=cmd_report)

    export = sub.add_parser("export", help="write the annual intensity CSV and Power BI CSVs")
    export.add_argument(
        "--annual-csv", default=str(REPO_ROOT / "exports" / "annual_grid_intensity.csv")
    )
    export.add_argument(
        "--powerbi-dir", default=None, help="also write the Power BI star schema here"
    )
    export.add_argument(
        "--half-hourly-days",
        type=int,
        default=365,
        help="days of half-hourly rows in the Power BI export",
    )
    export.set_defaults(func=cmd_export)

    site = sub.add_parser("site", help="build the static dashboard")
    site.add_argument("--out", default=str(REPO_ROOT / "site"))
    site.add_argument(
        "--annual-csv", default=str(REPO_ROOT / "exports" / "annual_grid_intensity.csv")
    )
    site.set_defaults(func=cmd_site)

    run = sub.add_parser("run", help="ingest, transform, forecast, backtest, export, site")
    run.add_argument("--as-of", help="treat this UTC time as 'now' (ISO 8601)")
    run.add_argument("--replay", metavar="DIR", help="serve HTTP from a recorded cassette")
    run.add_argument("--skip-backtest", action="store_true")
    run.add_argument("--test-days", type=int, default=BacktestConfig.test_days)
    run.add_argument(
        "--annual-csv", default=str(REPO_ROOT / "exports" / "annual_grid_intensity.csv")
    )
    run.add_argument("--powerbi-dir", default=None)
    run.add_argument("--site-dir", default=str(REPO_ROOT / "site"))
    _add_model_args(run)
    run.set_defaults(func=cmd_run)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        settings = Settings.from_env()
        code: int = args.func(args, settings)
    except (
        ConfigError,
        IngestError,
        CassetteMissError,
        TransformError,
        SeriesError,
        NotEnoughHistoryError,
        argparse.ArgumentTypeError,
    ) as exc:
        log.error("%s", exc)
        return 2
    return code


if __name__ == "__main__":
    raise SystemExit(main())

"""Incremental, idempotent ingestion of every raw dataset."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl

from gridwatch.config import Settings
from gridwatch.ingest import carbon_intensity as ci
from gridwatch.ingest.ember import EmberResult, ingest_ember_yearly
from gridwatch.ingest.http import FetchError, HttpFetcher
from gridwatch.ingest.storage import ParquetStore, UpsertStats, write_placeholder

log = logging.getLogger(__name__)

ALL_SOURCES = ("national", "generation", "regional", "snapshot", "ember")


def national_store(raw_dir: Path) -> ParquetStore:
    return ParquetStore(
        raw_dir / "carbon_intensity" / "national_intensity",
        ci.NATIONAL_SCHEMA,
        key_columns=["period_start_utc"],
        time_column="period_start_utc",
    )


def generation_store(raw_dir: Path) -> ParquetStore:
    return ParquetStore(
        raw_dir / "carbon_intensity" / "generation_mix",
        ci.GENERATION_SCHEMA,
        key_columns=["period_start_utc", "fuel"],
        time_column="period_start_utc",
    )


def regional_intensity_store(raw_dir: Path) -> ParquetStore:
    return ParquetStore(
        raw_dir / "carbon_intensity" / "regional_intensity",
        ci.REGIONAL_INTENSITY_SCHEMA,
        key_columns=["period_start_utc", "region_id"],
        time_column="period_start_utc",
    )


def regional_mix_store(raw_dir: Path) -> ParquetStore:
    return ParquetStore(
        raw_dir / "carbon_intensity" / "regional_generation_mix",
        ci.REGIONAL_MIX_SCHEMA,
        key_columns=["period_start_utc", "region_id", "fuel"],
        time_column="period_start_utc",
    )


def snapshot_store(raw_dir: Path) -> ParquetStore:
    return ParquetStore(
        raw_dir / "carbon_intensity" / "forecast_snapshots",
        ci.SNAPSHOT_SCHEMA,
        key_columns=["issued_at_utc", "period_start_utc"],
        time_column="issued_at_utc",
    )


@dataclass
class DatasetReport:
    name: str
    requests: int = 0
    windows: list[str] = field(default_factory=list)
    stats: UpsertStats = field(default_factory=UpsertStats)

    def as_dict(self) -> dict[str, object]:
        return {
            "dataset": self.name,
            "requests": self.requests,
            "windows": self.windows,
            "inserted": self.stats.inserted,
            "updated": self.stats.updated,
            "unchanged": self.stats.unchanged,
            "files_written": len(self.stats.files_written),
        }


@dataclass
class IngestReport:
    as_of_utc: str
    datasets: list[DatasetReport] = field(default_factory=list)
    ember: EmberResult | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "as_of_utc": self.as_of_utc,
            "datasets": [d.as_dict() for d in self.datasets],
            "ember": None if self.ember is None else self.ember.__dict__,
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2)


class IngestError(RuntimeError):
    """A dataset could not be fully ingested; rows fetched before the failure are kept."""


def _json(response_text: str, url: str) -> object:
    try:
        return json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise ci.ParseError(f"{url} did not return JSON: {exc}") from exc


def _ingest_ranges(
    name: str,
    dataset: ci.Dataset,
    stores: list[ParquetStore],
    parse: Callable[[object, datetime, ci.Window], Iterable[pl.DataFrame]],
    configured_start: datetime,
    fetcher: HttpFetcher,
    settings: Settings,
    now: datetime,
    repair_gaps: bool,
) -> DatasetReport:
    report = DatasetReport(name)
    lo, hi = stores[0].time_bounds()
    windows = ci.windows_to_fetch(
        configured_start, lo, hi, now, timedelta(days=settings.refetch_days)
    )
    if repair_gaps:
        gaps = ci.internal_gaps(stores[0].distinct_times())
        log.info("%s: re-requesting %d internal gap(s)", name, len(gaps))
        windows = gaps + windows
    report.windows = [f"{w.start:%Y-%m-%dT%H:%MZ}/{w.end:%Y-%m-%dT%H:%MZ}" for w in windows]
    plan = ci.plan_requests(settings.carbon_api_base, dataset, windows)
    log.info("%s: %d request(s) planned", name, len(plan))
    for done, (url, window) in enumerate(plan):
        try:
            response = fetcher.get(url)
            fetched_at = datetime.now(UTC)
            frames = list(parse(_json(response.text, url), fetched_at, window))
        except (FetchError, ci.ApiError, ci.ParseError) as exc:
            raise IngestError(
                f"{name}: request {done + 1} of {len(plan)} failed ({exc}). "
                f"Rows from the {done} earlier request(s) are saved; rerun to resume."
            ) from exc
        report.requests += 1
        for store, frame in zip(stores, frames, strict=True):
            report.stats.add(store.upsert(frame))
    return report


def ingest_national(
    fetcher: HttpFetcher, settings: Settings, now: datetime, repair_gaps: bool = False
) -> DatasetReport:
    return _ingest_ranges(
        "national_intensity",
        ci.Dataset.NATIONAL_INTENSITY,
        [national_store(settings.raw_dir)],
        lambda p, f, w: [ci.parse_national(p, f, w)],
        settings.national_start,
        fetcher,
        settings,
        now,
        repair_gaps,
    )


def ingest_generation(
    fetcher: HttpFetcher, settings: Settings, now: datetime, repair_gaps: bool = False
) -> DatasetReport:
    return _ingest_ranges(
        "generation_mix",
        ci.Dataset.GENERATION_MIX,
        [generation_store(settings.raw_dir)],
        lambda p, f, w: [ci.parse_generation(p, f, w)],
        settings.generation_start,
        fetcher,
        settings,
        now,
        repair_gaps,
    )


def ingest_regional(
    fetcher: HttpFetcher, settings: Settings, now: datetime, repair_gaps: bool = False
) -> DatasetReport:
    return _ingest_ranges(
        "regional",
        ci.Dataset.REGIONAL,
        [regional_intensity_store(settings.raw_dir), regional_mix_store(settings.raw_dir)],
        lambda p, f, w: list(ci.parse_regional(p, f, w)),
        settings.regional_start,
        fetcher,
        settings,
        now,
        repair_gaps,
    )


def ingest_snapshot(
    fetcher: HttpFetcher, settings: Settings, now: datetime, repair_gaps: bool = False
) -> DatasetReport:
    """Store the API's 48-hour forecast as issued now, for like-for-like accuracy checks."""
    report = DatasetReport("forecast_snapshots")
    issued = ci.floor_half_hour(now)
    url = ci.snapshot_url(settings.carbon_api_base, issued)
    report.windows = [f"{issued:%Y-%m-%dT%H:%MZ}/fw48h"]
    try:
        response = fetcher.get(url)
        frame = ci.parse_snapshot(_json(response.text, url), issued, datetime.now(UTC))
    except (FetchError, ci.ApiError, ci.ParseError) as exc:
        raise IngestError(f"forecast_snapshots: {exc}") from exc
    report.requests = 1
    report.stats.add(snapshot_store(settings.raw_dir).upsert(frame))
    return report


def run_ingest(
    settings: Settings,
    fetcher: HttpFetcher,
    now: datetime,
    sources: Iterable[str] = ALL_SOURCES,
    repair_gaps: bool = False,
) -> IngestReport:
    """Ingest the selected sources. Each source is independent and resumable.

    ``repair_gaps`` also re-requests every hole inside the stored history. Daily runs leave
    it off because the API has a few permanent gaps that would be re-requested each time.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    selected = list(dict.fromkeys(sources))
    unknown = sorted(set(selected) - set(ALL_SOURCES))
    if unknown:
        raise ValueError(f"unknown source(s) {unknown}; choose from {list(ALL_SOURCES)}")
    report = IngestReport(as_of_utc=now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
    runners = {
        "national": ingest_national,
        "generation": ingest_generation,
        "regional": ingest_regional,
        "snapshot": ingest_snapshot,
    }
    for source in selected:
        if source == "ember":
            report.ember = ingest_ember_yearly(
                fetcher, settings.ember_yearly_url, settings.raw_dir, now
            )
            log.info("ember: %s (%d rows)", report.ember.status, report.ember.rows)
            continue
        dataset_report = runners[source](fetcher, settings, now, repair_gaps)
        log.info("%s: %s", source, dataset_report.as_dict())
        report.datasets.append(dataset_report)
    return report


def ensure_empty_datasets(raw_dir: Path) -> None:
    """Write an empty file for any dataset with no data, so dbt sources always resolve."""
    for store in (
        national_store(raw_dir),
        generation_store(raw_dir),
        regional_intensity_store(raw_dir),
        regional_mix_store(raw_dir),
        snapshot_store(raw_dir),
    ):
        write_placeholder(store)

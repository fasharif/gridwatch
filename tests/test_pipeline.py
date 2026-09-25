from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import polars as pl
import pytest

from gridwatch.config import Settings
from gridwatch.ingest.cassette import ReplayTransport
from gridwatch.ingest.http import HttpFetcher
from gridwatch.ingest.pipeline import (
    IngestError,
    ensure_empty_datasets,
    generation_store,
    national_store,
    regional_intensity_store,
    regional_mix_store,
    run_ingest,
    snapshot_store,
)
from tests.fakes import FakeCarbonApi
from tests.helpers import CASSETTE

CI_SOURCES = ["national", "generation", "regional", "snapshot"]


def fake_settings(factory: Callable[..., Settings], start: str) -> Settings:
    return factory(
        GRIDWATCH_NATIONAL_START=start,
        GRIDWATCH_GENERATION_START=start,
        GRIDWATCH_REGIONAL_START=start,
        GRIDWATCH_REFETCH_DAYS="1",
    )


def half_hours(start: datetime, end: datetime) -> int:
    return int((end - start) / timedelta(minutes=30))


def test_first_run_then_idempotent_rerun(
    settings_factory: Callable[..., Settings], fetcher_factory: Callable[..., HttpFetcher]
) -> None:
    settings = fake_settings(settings_factory, "2026-03-01")
    api = FakeCarbonApi()
    fetcher = fetcher_factory(httpx.MockTransport(api))
    now = datetime(2026, 3, 20, 9, 10, tzinfo=UTC)
    run_ingest(settings, fetcher, now, CI_SOURCES)
    expected = half_hours(datetime(2026, 3, 1, tzinfo=UTC), datetime(2026, 3, 20, 9, tzinfo=UTC))
    national = national_store(settings.raw_dir).read()
    assert national.height == expected
    assert generation_store(settings.raw_dir).read().height == expected * 9
    assert regional_intensity_store(settings.raw_dir).read().height == expected * 3
    assert regional_mix_store(settings.raw_dir).read().height == expected * 27
    assert snapshot_store(settings.raw_dir).read().height == 97

    files = sorted((settings.raw_dir).rglob("*.parquet"))
    before = {p: p.read_bytes() for p in files}
    report = run_ingest(settings, fetcher, now, CI_SOURCES)
    assert all(d.stats.inserted == 0 and d.stats.updated == 0 for d in report.datasets)
    assert {p: p.read_bytes() for p in files} == before


def test_incremental_run_fetches_only_recent_window(
    settings_factory: Callable[..., Settings], fetcher_factory: Callable[..., HttpFetcher]
) -> None:
    settings = fake_settings(settings_factory, "2026-03-01")
    api = FakeCarbonApi()
    fetcher = fetcher_factory(httpx.MockTransport(api))
    run_ingest(settings, fetcher, datetime(2026, 3, 20, tzinfo=UTC), ["national"])
    api.requests.clear()
    api.actual_offset = 5  # the API revised its recent actuals
    report = run_ingest(settings, fetcher, datetime(2026, 3, 21, tzinfo=UTC), ["national"])
    assert api.requests == [
        "https://api.carbonintensity.org.uk/intensity/2026-03-19T00:30Z/2026-03-21T00:00Z"
    ]
    stats = report.datasets[0].stats
    assert stats.inserted == 48
    assert stats.updated == 48  # the day re-fetched from existing history


def test_year_boundary_is_complete(
    settings_factory: Callable[..., Settings], fetcher_factory: Callable[..., HttpFetcher]
) -> None:
    settings = fake_settings(settings_factory, "2025-12-20")
    fetcher = fetcher_factory(httpx.MockTransport(FakeCarbonApi()))
    now = datetime(2026, 1, 10, tzinfo=UTC)
    run_ingest(settings, fetcher, now, ["generation", "regional"])
    starts = generation_store(settings.raw_dir).read()["period_start_utc"].unique().sort()
    assert starts.len() == half_hours(datetime(2025, 12, 20, tzinfo=UTC), now)
    assert datetime(2025, 12, 31, 23, 30) in starts.to_list()
    regional = regional_intensity_store(settings.raw_dir).read()
    assert regional["period_start_utc"].n_unique() == starts.len()


def test_failure_keeps_earlier_chunks_and_resumes(
    settings_factory: Callable[..., Settings], fetcher_factory: Callable[..., HttpFetcher]
) -> None:
    settings = fake_settings(settings_factory, "2026-01-01")
    api = FakeCarbonApi(fail_on={2: 404})
    fetcher = fetcher_factory(httpx.MockTransport(api))
    now = datetime(2026, 3, 1, tzinfo=UTC)
    with pytest.raises(IngestError, match=r"request 2 of 2 failed.*rerun to resume"):
        run_ingest(settings, fetcher, now, ["national"])
    saved = national_store(settings.raw_dir).read()
    assert saved.height == 30 * 48
    run_ingest(settings, fetcher, now, ["national"])
    assert national_store(settings.raw_dir).read().height == half_hours(
        datetime(2026, 1, 1, tzinfo=UTC), now
    )


def test_repair_gaps_requests_only_the_holes(
    settings_factory: Callable[..., Settings], fetcher_factory: Callable[..., HttpFetcher]
) -> None:
    settings = fake_settings(settings_factory, "2026-02-01")
    hole = {datetime(2026, 2, 3, 10, tzinfo=UTC), datetime(2026, 2, 3, 10, 30, tzinfo=UTC)}
    api = FakeCarbonApi(missing=set(hole))
    fetcher = fetcher_factory(httpx.MockTransport(api))
    now = datetime(2026, 2, 10, tzinfo=UTC)
    run_ingest(settings, fetcher, now, ["national"])
    assert (
        national_store(settings.raw_dir).read().height
        == half_hours(datetime(2026, 2, 1, tzinfo=UTC), now) - 2
    )
    api.missing.clear()
    api.requests.clear()
    run_ingest(settings, fetcher, now, ["national"], repair_gaps=True)
    assert api.requests[0].endswith("/intensity/2026-02-03T10:30Z/2026-02-03T11:00Z")
    assert national_store(settings.raw_dir).read().height == half_hours(
        datetime(2026, 2, 1, tzinfo=UTC), now
    )


def test_rejects_unknown_source_and_naive_time(
    settings_factory: Callable[..., Settings], fetcher_factory: Callable[..., HttpFetcher]
) -> None:
    settings = settings_factory()
    fetcher = fetcher_factory(httpx.MockTransport(FakeCarbonApi()))
    with pytest.raises(ValueError, match="unknown source"):
        run_ingest(settings, fetcher, datetime(2026, 1, 1, tzinfo=UTC), ["weather"])
    with pytest.raises(ValueError, match="timezone-aware"):
        run_ingest(settings, fetcher, datetime(2026, 1, 1), ["national"])


def test_replaying_the_recorded_cassette(
    settings_factory: Callable[..., Settings],
    fetcher_factory: Callable[..., HttpFetcher],
    fixture_env: dict[str, str],
) -> None:
    env = {k: v for k, v in fixture_env.items() if k.startswith("GRIDWATCH_")}
    settings = settings_factory(**env)
    transport = ReplayTransport(CASSETTE)
    as_of = datetime.fromisoformat(fixture_env["FIXTURE_AS_OF"]).replace(tzinfo=UTC)
    report = run_ingest(settings, fetcher_factory(transport), as_of)
    assert report.ember is not None
    assert report.ember.status == "downloaded"
    national = national_store(settings.raw_dir).read()
    assert national["period_start_utc"].max() == datetime(2026, 9, 19, 23, 30)
    assert national.filter(pl.col("actual_gco2_kwh").is_null()).height < 5
    assert len(transport.requests) == len(transport.index)


def test_placeholders_for_missing_datasets(settings_factory: Callable[..., Settings]) -> None:
    settings = settings_factory()
    ensure_empty_datasets(settings.raw_dir)
    for make in (
        national_store,
        generation_store,
        regional_intensity_store,
        regional_mix_store,
        snapshot_store,
    ):
        store = make(settings.raw_dir)
        assert store.read().height == 0
        assert store.files()


def test_report_serialises(tmp_path: Path) -> None:
    from gridwatch.ingest.pipeline import IngestReport

    assert '"as_of_utc": "x"' in IngestReport(as_of_utc="x").to_json()

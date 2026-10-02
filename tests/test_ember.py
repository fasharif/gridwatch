from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from gridwatch.config import EMBER_YEARLY_CSV, Settings
from gridwatch.ingest.ember import EmberFormatError, ingest_ember_yearly, parse_yearly_csv
from gridwatch.ingest.http import HttpFetcher
from gridwatch.ingest.pipeline import IngestError, ingest_ember
from tests.helpers import cassette_body

NOW = datetime(2026, 9, 20, tzinfo=UTC)


def csv_body() -> bytes:
    return cassette_body("release_generation_yearly_global.csv")


def test_parse_recorded_csv() -> None:
    frame = parse_yearly_csv(csv_body())
    uae = frame.filter(
        (frame["area"] == "United Arab Emirates")
        & (frame["electricity_source"] == "Total generation")
    )
    assert uae.height > 30
    assert uae["iso3_code"].unique().to_list() == ["ARE"]
    assert frame["is_aggregated_source"].null_count() == 0
    assert frame["emissions_intensity_gco2e_kwh"].dtype.is_float()


def test_missing_column_is_reported() -> None:
    with pytest.raises(EmberFormatError, match="missing expected columns"):
        parse_yearly_csv(b"Area,Year\nX,2020\n")


def test_wrong_type_is_reported() -> None:
    header, first = csv_body().decode("utf-8").splitlines()[:2]
    broken = first.replace(first.split(",")[2], "twenty", 1)
    with pytest.raises(EmberFormatError, match="wrong type"):
        parse_yearly_csv(f"{header}\n{broken}\n".encode())


def test_empty_file_is_reported() -> None:
    header = csv_body().decode("utf-8").splitlines()[0]
    with pytest.raises(EmberFormatError, match="no rows"):
        parse_yearly_csv(f"{header}\n".encode())


def test_conditional_download(tmp_path: Path, fetcher_factory: Callable[..., HttpFetcher]) -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.headers))
        if request.headers.get("If-None-Match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(
            200,
            content=csv_body(),
            headers={"ETag": '"v1"', "Last-Modified": "Tue, 22 Sep 2026 16:24:55 GMT"},
        )

    fetcher = fetcher_factory(httpx.MockTransport(handler))
    first = ingest_ember_yearly(fetcher, EMBER_YEARLY_CSV, tmp_path, NOW)
    second = ingest_ember_yearly(fetcher, EMBER_YEARLY_CSV, tmp_path, NOW)
    assert first.status == "downloaded"
    assert second.status == "not-modified"
    assert second.rows == first.rows
    assert "if-none-match" not in seen[0]
    assert seen[1]["if-none-match"] == '"v1"'
    assert seen[1]["if-modified-since"] == "Tue, 22 Sep 2026 16:24:55 GMT"
    assert (tmp_path / "ember" / "yearly_electricity" / "yearly_electricity.parquet").exists()


def test_unchanged_content_without_etag_is_not_rewritten(
    tmp_path: Path, fetcher_factory: Callable[..., HttpFetcher]
) -> None:
    fetcher = fetcher_factory(
        httpx.MockTransport(lambda request: httpx.Response(200, content=csv_body()))
    )
    ingest_ember_yearly(fetcher, EMBER_YEARLY_CSV, tmp_path, NOW)
    target = tmp_path / "ember" / "yearly_electricity" / "yearly_electricity.parquet"
    stamp = target.stat().st_mtime_ns
    assert ingest_ember_yearly(fetcher, EMBER_YEARLY_CSV, tmp_path, NOW).status == "not-modified"
    assert target.stat().st_mtime_ns == stamp


def test_failed_download_keeps_the_previous_file(
    settings_factory: Callable[..., Settings],
    fetcher_factory: Callable[..., HttpFetcher],
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = settings_factory()
    good = fetcher_factory(
        httpx.MockTransport(lambda request: httpx.Response(200, content=csv_body()))
    )
    first = ingest_ember(good, settings, NOW)
    moved = fetcher_factory(httpx.MockTransport(lambda request: httpx.Response(404)))
    kept = ingest_ember(moved, settings, NOW)
    assert kept.status == "failed-kept-previous"
    assert (kept.rows, kept.sha256) == (first.rows, first.sha256)
    assert kept.error is not None
    assert "HTTP 404" in kept.error
    assert "Keeping the file fetched at" in caplog.text
    reshaped = fetcher_factory(
        httpx.MockTransport(lambda request: httpx.Response(200, content=b"Area,Year\nX,2024\n"))
    )
    assert ingest_ember(reshaped, settings, NOW).status == "failed-kept-previous"


def test_failed_first_download_stops_the_run(
    settings_factory: Callable[..., Settings], fetcher_factory: Callable[..., HttpFetcher]
) -> None:
    fetcher = fetcher_factory(httpx.MockTransport(lambda request: httpx.Response(404)))
    with pytest.raises(IngestError, match="No earlier Ember file is stored"):
        ingest_ember(fetcher, settings_factory(), NOW)

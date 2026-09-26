"""Publishing the API forecast snapshots with the site and restoring them from it."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import polars as pl
import pytest

from gridwatch.ingest.carbon_intensity import SNAPSHOT_SCHEMA
from gridwatch.ingest.http import HttpFetcher
from gridwatch.ingest.pipeline import snapshot_store
from gridwatch.ingest.snapshot_mirror import (
    SnapshotMirrorError,
    publish_snapshots,
    restore_snapshots,
)

BASE = "https://example.test/gridwatch/data/forecast_snapshots/"


def snapshot_rows(issued: datetime, periods: int = 4) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "issued_at_utc": [issued] * periods,
            "period_start_utc": [issued + timedelta(minutes=30 * i) for i in range(periods)],
            "period_end_utc": [issued + timedelta(minutes=30 * (i + 1)) for i in range(periods)],
            "forecast_gco2_kwh": [100 + i for i in range(periods)],
            "intensity_index": ["low"] * periods,
            "fetched_at_utc": [issued] * periods,
        },
        schema=SNAPSHOT_SCHEMA,
    )


def serve(folder: Path, broken: set[str] | None = None) -> httpx.MockTransport:
    """Serve ``folder`` the way GitHub Pages would, 404 for anything missing."""

    def handler(request: httpx.Request) -> httpx.Response:
        relative = request.url.path.removeprefix("/gridwatch/data/forecast_snapshots/")
        target = folder / relative
        if not target.is_file():
            return httpx.Response(404, text="Not Found")
        content = target.read_bytes()
        if broken and relative in broken:
            content = content[:-1] + b"x"
        return httpx.Response(200, content=content)

    return httpx.MockTransport(handler)


@pytest.fixture
def published(tmp_path: Path) -> Path:
    source = tmp_path / "source_raw"
    store = snapshot_store(source)
    store.upsert(snapshot_rows(datetime(2026, 8, 31, 5, 0)))
    store.upsert(snapshot_rows(datetime(2026, 9, 1, 5, 0)))
    site = tmp_path / "site" / "data" / "forecast_snapshots"
    assert publish_snapshots(source, site) == 8
    return site


def test_publish_writes_files_and_manifest(published: Path) -> None:
    manifest = json.loads((published / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["rows"] == 8
    assert [f["path"] for f in manifest["files"]] == [
        "2026/2026-08.parquet",
        "2026/2026-09.parquet",
    ]
    assert "CC BY 4.0" in manifest["source"]
    assert (published / "2026" / "2026-09.parquet").is_file()


def test_publish_without_snapshots_writes_nothing(tmp_path: Path) -> None:
    assert publish_snapshots(tmp_path / "raw", tmp_path / "out") == 0
    assert not (tmp_path / "out").exists()


def test_restore_merges_into_an_empty_store_then_is_idempotent(
    published: Path, tmp_path: Path, fetcher_factory: Callable[..., HttpFetcher]
) -> None:
    raw = tmp_path / "restored_raw"
    fetcher = fetcher_factory(serve(published))
    first = restore_snapshots(fetcher, BASE, raw)
    assert (first.status, first.files, first.stats.inserted) == ("restored", 2, 8)
    restored = snapshot_store(raw).read()
    assert restored.height == 8
    second = restore_snapshots(fetcher, BASE, raw)
    assert (second.stats.inserted, second.stats.unchanged) == (0, 8)


def test_restore_keeps_local_snapshots_the_site_does_not_have(
    published: Path, tmp_path: Path, fetcher_factory: Callable[..., HttpFetcher]
) -> None:
    raw = tmp_path / "raw"
    snapshot_store(raw).upsert(snapshot_rows(datetime(2026, 9, 2, 5, 0)))
    restore_snapshots(fetcher_factory(serve(published)), BASE, raw)
    issued = snapshot_store(raw).read()["issued_at_utc"].unique().sort().to_list()
    assert issued == [
        datetime(2026, 8, 31, 5, 0),
        datetime(2026, 9, 1, 5, 0),
        datetime(2026, 9, 2, 5, 0),
    ]


def test_nothing_published_yet_is_not_an_error(
    tmp_path: Path, fetcher_factory: Callable[..., HttpFetcher]
) -> None:
    result = restore_snapshots(fetcher_factory(serve(tmp_path / "empty")), BASE, tmp_path)
    assert result.status == "no-mirror"
    assert result.files == 0


def test_a_corrupted_file_stops_the_restore(
    published: Path, tmp_path: Path, fetcher_factory: Callable[..., HttpFetcher]
) -> None:
    fetcher = fetcher_factory(serve(published, broken={"2026/2026-09.parquet"}))
    with pytest.raises(SnapshotMirrorError, match="does not match the SHA-256"):
        restore_snapshots(fetcher, BASE, tmp_path / "raw")


def test_an_unavailable_site_stops_the_restore(
    tmp_path: Path, fetcher_factory: Callable[..., HttpFetcher]
) -> None:
    fetcher = fetcher_factory(httpx.MockTransport(lambda request: httpx.Response(503)))
    with pytest.raises(SnapshotMirrorError, match="could not read the snapshot manifest"):
        restore_snapshots(fetcher, BASE, tmp_path / "raw")


@pytest.mark.parametrize(
    "manifest",
    [
        b"not json",
        json.dumps({"files": [{"path": "../../etc/passwd", "sha256": "x"}]}).encode(),
    ],
)
def test_a_bad_manifest_stops_the_restore(
    manifest: bytes, tmp_path: Path, fetcher_factory: Callable[..., HttpFetcher]
) -> None:
    fetcher = fetcher_factory(
        httpx.MockTransport(lambda request: httpx.Response(200, content=manifest))
    )
    with pytest.raises(SnapshotMirrorError):
        restore_snapshots(fetcher, BASE, tmp_path / "raw")

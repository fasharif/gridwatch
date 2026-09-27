"""Keep the API's forecast snapshots somewhere more durable than the Actions cache.

Every other raw dataset can be fetched again from the API. The 48-hour forecast *as issued*
cannot: the API keeps only its latest forecast for each half-hour, so a snapshot that is lost
is lost for good. The daily workflow therefore publishes the snapshot files with the
dashboard (``site/data/forecast_snapshots/``) and restores them from the live site before it
ingests, merging them into the local store with the usual idempotent upsert.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from gridwatch.ingest.http import FetchError, HttpFetcher
from gridwatch.ingest.pipeline import snapshot_store
from gridwatch.ingest.storage import SchemaMismatchError, UpsertStats

log = logging.getLogger(__name__)

MANIFEST = "manifest.json"
ATTRIBUTION = "Carbon Intensity API, National Energy System Operator (CC BY 4.0)"
FILE_PATTERN = re.compile(r"^\d{4}/\d{4}-\d{2}\.parquet$")


class SnapshotMirrorError(RuntimeError):
    """The published snapshots could not be read or did not match their manifest."""


@dataclass
class RestoreResult:
    status: str  # "restored" or "no-mirror"
    files: int = 0
    stats: UpsertStats = field(default_factory=UpsertStats)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def publish_snapshots(raw_dir: Path, out_dir: Path) -> int:
    """Copy every stored snapshot file to ``out_dir`` with a manifest; returns the row count.

    Only monthly data files are published. The empty placeholder that ``ensure_empty_datasets``
    writes for a store with no data is skipped: publishing it would put a path into the
    manifest that ``restore_snapshots`` rightly refuses, and every later run would stop.
    """
    store = snapshot_store(raw_dir)
    files = [
        path
        for path in store.files()
        if FILE_PATTERN.match(path.relative_to(store.root).as_posix())
    ]
    if not files:
        return 0
    if out_dir.exists():
        shutil.rmtree(out_dir)
    entries: list[dict[str, object]] = []
    rows = 0
    for source in files:
        relative = source.relative_to(store.root).as_posix()
        target = out_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        content = target.read_bytes()
        height = pl.read_parquet(io.BytesIO(content)).height
        rows += height
        entries.append({"path": relative, "rows": height, "sha256": _sha256(content)})
    manifest = {
        "dataset": "Carbon Intensity API 48-hour national forecast, as issued on each run",
        "source": ATTRIBUTION,
        "generated_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rows": rows,
        "files": entries,
    }
    (out_dir / MANIFEST).write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return rows


def restore_snapshots(fetcher: HttpFetcher, base_url: str, raw_dir: Path) -> RestoreResult:
    """Merge the snapshots published at ``base_url`` into the local store.

    A missing manifest (HTTP 404) means nothing has been published yet, which is normal before
    the first deploy. Any other failure raises, so the workflow stops before it could publish
    a site with fewer snapshots than the one already live.
    """
    base = base_url.rstrip("/") + "/"
    try:
        response = fetcher.get(base + MANIFEST)
    except FetchError as exc:
        if exc.status == 404:
            log.info("no published snapshots at %s yet", base)
            return RestoreResult("no-mirror")
        raise SnapshotMirrorError(f"could not read the snapshot manifest: {exc}") from exc
    try:
        manifest = json.loads(response.text)
        entries = list(manifest["files"])
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise SnapshotMirrorError(f"{base}{MANIFEST} is not a snapshot manifest") from exc
    store = snapshot_store(raw_dir)
    result = RestoreResult("restored")
    for entry in entries:
        if not isinstance(entry, dict):
            raise SnapshotMirrorError(f"unexpected entry in the manifest: {entry!r}")
        path = str(entry.get("path", ""))
        if not FILE_PATTERN.match(path):
            raise SnapshotMirrorError(f"unexpected file name in the manifest: {path!r}")
        try:
            content = fetcher.get(base + path).content
        except FetchError as exc:
            raise SnapshotMirrorError(f"could not download {path}: {exc}") from exc
        if _sha256(content) != entry.get("sha256"):
            raise SnapshotMirrorError(f"{path} does not match the SHA-256 in the manifest")
        try:
            result.stats.add(store.upsert(pl.read_parquet(io.BytesIO(content))))
        except (pl.exceptions.PolarsError, SchemaMismatchError) as exc:
            raise SnapshotMirrorError(f"{path} is not a snapshot file: {exc}") from exc
        result.files += 1
    log.info(
        "restored %d snapshot file(s): %d new row(s), %d unchanged",
        result.files,
        result.stats.inserted,
        result.stats.unchanged,
    )
    return result

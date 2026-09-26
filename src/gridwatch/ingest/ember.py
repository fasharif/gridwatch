"""Ember yearly electricity data (CC BY 4.0).

Ember's API needs a key, so gridwatch uses the public bulk CSV instead. The file is about
16 MB and changes a couple of times a month, so it is fetched with a conditional GET
(``If-None-Match`` / ``If-Modified-Since``). When Ember answers 304 nothing is rewritten.
The CSV is stored unmodified in content, with snake_case column names, as Parquet.
"""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from gridwatch.ingest.http import HttpFetcher
from gridwatch.ingest.storage import write_single

# Source column -> (raw column, type). A missing source column is a hard error, so a
# format change at Ember fails loudly instead of silently producing empty marts.
EMBER_COLUMNS: Mapping[str, tuple[str, pl.DataType]] = {
    "Area": ("area", pl.String()),
    "ISO 3 code": ("iso3_code", pl.String()),
    "Year": ("year", pl.Int32()),
    "Area type": ("area_type", pl.String()),
    "Electricity source": ("electricity_source", pl.String()),
    "Is aggregated source": ("is_aggregated_source", pl.Boolean()),
    "Generation (TWh)": ("generation_twh", pl.Float64()),
    "Share of generation (%)": ("share_of_generation_pct", pl.Float64()),
    "Capacity (GW)": ("capacity_gw", pl.Float64()),
    "Emissions (MtCO2e)": ("emissions_mtco2e", pl.Float64()),
    "Emissions intensity (gCO2e/kWh)": ("emissions_intensity_gco2e_kwh", pl.Float64()),
    "EU member": ("is_eu_member", pl.Boolean()),
}


class EmberFormatError(ValueError):
    """The Ember CSV no longer has the columns gridwatch expects."""


@dataclass(frozen=True)
class EmberManifest:
    url: str
    etag: str | None
    last_modified: str | None
    sha256: str
    rows: int
    fetched_at_utc: str

    @classmethod
    def load(cls, path: Path) -> EmberManifest | None:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(**data)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8", newline="\n")


@dataclass(frozen=True)
class EmberResult:
    status: str  # "downloaded", "not-modified" or "failed-kept-previous"
    rows: int
    sha256: str
    error: str | None = None


def _paths(raw_dir: Path) -> tuple[Path, Path]:
    target_dir = raw_dir / "ember" / "yearly_electricity"
    return target_dir / "manifest.json", target_dir / "yearly_electricity.parquet"


def previous_download(raw_dir: Path) -> EmberManifest | None:
    """The manifest of the Ember file already stored, if there is one."""
    manifest_path, parquet_path = _paths(raw_dir)
    if not parquet_path.exists():
        return None
    return EmberManifest.load(manifest_path)


def parse_yearly_csv(content: bytes) -> pl.DataFrame:
    """Parse Ember's yearly CSV into the raw schema."""
    try:
        frame = pl.read_csv(io.BytesIO(content), infer_schema=False)
    except pl.exceptions.ComputeError as exc:
        raise EmberFormatError(f"could not read the Ember CSV: {exc}") from exc
    missing = [c for c in EMBER_COLUMNS if c not in frame.columns]
    if missing:
        raise EmberFormatError(f"Ember CSV is missing expected columns: {missing}")
    exprs = []
    for source, (target, dtype) in EMBER_COLUMNS.items():
        col = pl.col(source).str.strip_chars()
        col = pl.when(col == "").then(None).otherwise(col)
        if dtype == pl.Boolean():
            exprs.append(
                col.str.to_lowercase()
                .replace_strict(
                    {"true": True, "false": False}, default=None, return_dtype=pl.Boolean()
                )
                .alias(target)
            )
        else:
            exprs.append(col.cast(dtype, strict=True).alias(target))
    try:
        parsed = frame.select(exprs)
    except pl.exceptions.InvalidOperationError as exc:
        raise EmberFormatError(f"Ember CSV has a value of the wrong type: {exc}") from exc
    if parsed.height == 0:
        raise EmberFormatError("Ember CSV has no rows")
    if parsed["year"].null_count() or parsed["area"].null_count():
        raise EmberFormatError("Ember CSV has rows without an area or year")
    return parsed.sort(["area", "year", "electricity_source"])


def ingest_ember_yearly(
    fetcher: HttpFetcher, url: str, raw_dir: Path, now: datetime | None = None
) -> EmberResult:
    """Download the yearly CSV if it changed and store it as Parquet."""
    manifest_path, parquet_path = _paths(raw_dir)
    manifest = EmberManifest.load(manifest_path)
    headers: dict[str, str] = {}
    if manifest is not None and parquet_path.exists() and manifest.url == url:
        if manifest.etag:
            headers["If-None-Match"] = manifest.etag
        if manifest.last_modified:
            headers["If-Modified-Since"] = manifest.last_modified
    response = fetcher.get(url, headers=headers)
    if response.status_code == 304 and manifest is not None:
        return EmberResult("not-modified", manifest.rows, manifest.sha256)
    content = response.content
    digest = hashlib.sha256(content).hexdigest()
    if manifest is not None and manifest.sha256 == digest and parquet_path.exists():
        return EmberResult("not-modified", manifest.rows, digest)
    frame = parse_yearly_csv(content)
    write_single(frame, parquet_path)
    fetched = (now or datetime.now(UTC)).astimezone(UTC)
    EmberManifest(
        url=url,
        etag=response.headers.get("ETag"),
        last_modified=response.headers.get("Last-Modified"),
        sha256=digest,
        rows=frame.height,
        fetched_at_utc=fetched.strftime("%Y-%m-%dT%H:%M:%SZ"),
    ).save(manifest_path)
    return EmberResult("downloaded", frame.height, digest)

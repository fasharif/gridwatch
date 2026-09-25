"""Raw Parquet storage with idempotent upserts.

Each dataset lives in its own directory, split into one file per calendar month of the
time column (``2025/2025-01.parquet``). An upsert only rewrites the months it touches, and
only when a row is new or its values changed. Running the same ingestion twice therefore
leaves every file byte-for-byte unchanged, and a crash mid-run never corrupts a month file
because each write goes to a temporary file that is then atomically renamed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

METADATA_COLUMNS = frozenset({"fetched_at_utc"})
# Written for a dataset with no data yet, so readers that glob the directory still work.
PLACEHOLDER = Path("empty") / "empty.parquet"


@dataclass
class UpsertStats:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    files_written: list[str] = field(default_factory=list)

    def add(self, other: UpsertStats) -> None:
        self.inserted += other.inserted
        self.updated += other.updated
        self.unchanged += other.unchanged
        self.files_written.extend(other.files_written)


class SchemaMismatchError(ValueError):
    """Incoming rows do not match the dataset schema."""


class ParquetStore:
    """A monthly-partitioned Parquet dataset keyed by ``key_columns``."""

    def __init__(
        self,
        root: Path,
        schema: Mapping[str, pl.DataType],
        key_columns: Sequence[str],
        time_column: str,
    ) -> None:
        missing = [c for c in [*key_columns, time_column] if c not in schema]
        if missing:
            raise ValueError(f"key/time columns {missing} are not in the schema")
        self.root = root
        self.schema = dict(schema)
        self.key_columns = list(key_columns)
        self.time_column = time_column
        self.value_columns = [
            c for c in schema if c not in self.key_columns and c not in METADATA_COLUMNS
        ]

    def files(self) -> list[Path]:
        if not self.root.exists():
            return []
        return sorted(p for p in self.root.glob("*/*.parquet") if p.is_file())

    def _path_for(self, year: int, month: int) -> Path:
        return self.root / f"{year:04d}" / f"{year:04d}-{month:02d}.parquet"

    def read(self) -> pl.DataFrame:
        files = self.files()
        if not files:
            return pl.DataFrame(schema=self.schema)
        return pl.concat([pl.read_parquet(p) for p in files], how="vertical").select(
            list(self.schema)
        )

    def time_bounds(self) -> tuple[datetime | None, datetime | None]:
        """Earliest and latest value of the time column, as aware UTC datetimes."""
        files = self.files()
        if not files:
            return None, None
        lazy = pl.scan_parquet([str(p) for p in files]).select(
            pl.col(self.time_column).min().alias("lo"), pl.col(self.time_column).max().alias("hi")
        )
        row = lazy.collect().row(0)
        lo, hi = row
        return (_aware(lo), _aware(hi))

    def distinct_times(self) -> list[datetime]:
        """Every distinct value of the time column, as aware UTC datetimes, sorted."""
        files = self.files()
        if not files:
            return []
        series = (
            pl.scan_parquet([str(p) for p in files])
            .select(pl.col(self.time_column).unique().sort())
            .collect()
            .to_series()
        )
        return [value.replace(tzinfo=UTC) for value in series.to_list()]

    def _conform(self, frame: pl.DataFrame) -> pl.DataFrame:
        extra = set(frame.columns) - set(self.schema)
        missing = set(self.schema) - set(frame.columns)
        if extra or missing:
            raise SchemaMismatchError(
                f"columns differ from the {self.root.name} schema "
                f"(missing: {sorted(missing)}, unexpected: {sorted(extra)})"
            )
        return frame.select([pl.col(c).cast(t, strict=True) for c, t in self.schema.items()])

    def upsert(self, frame: pl.DataFrame) -> UpsertStats:
        """Insert new rows and replace rows whose values changed; returns counts."""
        stats = UpsertStats()
        if frame.height == 0:
            return stats
        incoming = self._conform(frame)
        null_keys = incoming.select(
            pl.any_horizontal(pl.col(self.key_columns).is_null())
        ).to_series()
        if null_keys.any():
            raise SchemaMismatchError(f"{int(null_keys.sum())} incoming rows have a null key")
        # The last occurrence of a key wins inside one batch.
        incoming = incoming.unique(subset=self.key_columns, keep="last", maintain_order=True)
        incoming = incoming.with_columns(
            pl.col(self.time_column).dt.year().alias("__y"),
            pl.col(self.time_column).dt.month().alias("__m"),
        )
        for (year, month), part in incoming.group_by(["__y", "__m"], maintain_order=True):
            stats.add(self._upsert_month(int(str(year)), int(str(month)), part.drop("__y", "__m")))
        return stats

    def _upsert_month(self, year: int, month: int, new: pl.DataFrame) -> UpsertStats:
        path = self._path_for(year, month)
        existing = pl.read_parquet(path) if path.exists() else pl.DataFrame(schema=self.schema)
        existing = existing.select(list(self.schema))
        compare = (
            existing.select([*self.key_columns, *self.value_columns])
            .rename({c: f"{c}__old" for c in self.value_columns})
            .with_columns(pl.lit(True).alias("__present"))
        )
        joined = new.join(compare, on=self.key_columns, how="left", nulls_equal=True)
        present = pl.col("__present").fill_null(False)
        differs = (
            pl.any_horizontal(
                [pl.col(c).ne_missing(pl.col(f"{c}__old")) for c in self.value_columns]
            )
            if self.value_columns
            else pl.lit(False)
        )
        flagged = joined.with_columns(
            (~present).alias("__insert"), (present & differs).alias("__update")
        )
        inserted = int(flagged["__insert"].sum())
        updated = int(flagged["__update"].sum())
        stats = UpsertStats(
            inserted=inserted, updated=updated, unchanged=new.height - inserted - updated
        )
        if inserted == 0 and updated == 0:
            return stats
        changed = flagged.filter(pl.col("__insert") | pl.col("__update")).select(list(self.schema))
        kept = existing.join(
            changed.select(self.key_columns), on=self.key_columns, how="anti", nulls_equal=True
        )
        merged = pl.concat([kept, changed], how="vertical").sort(self.key_columns)
        _atomic_write(merged, path)
        stats.files_written.append(str(path))
        placeholder = self.root / PLACEHOLDER
        if placeholder.exists():
            placeholder.unlink()
        return stats


def _atomic_write(frame: pl.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    frame.write_parquet(tmp, compression="zstd", statistics=True)
    tmp.replace(path)


def write_placeholder(store: ParquetStore) -> None:
    """Write an empty file with the dataset schema if the dataset has no files yet."""
    if not store.files():
        path = store.root / PLACEHOLDER
        path.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(schema=store.schema).write_parquet(path)


def write_single(frame: pl.DataFrame, path: Path) -> None:
    """Atomically write one un-partitioned Parquet file."""
    _atomic_write(frame, path)


def _aware(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise TypeError(f"expected a datetime, got {type(value).__name__}")
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

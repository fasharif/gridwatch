from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest

from gridwatch.ingest.storage import (
    PLACEHOLDER,
    ParquetStore,
    SchemaMismatchError,
    write_placeholder,
)

SCHEMA: dict[str, pl.DataType] = {
    "period_start_utc": pl.Datetime("us"),
    "value": pl.Int32(),
    "fetched_at_utc": pl.Datetime("us"),
}


def store(tmp_path: Path) -> ParquetStore:
    return ParquetStore(tmp_path / "ds", SCHEMA, ["period_start_utc"], "period_start_utc")


def frame(
    rows: list[tuple[datetime, int]], fetched: datetime = datetime(2026, 1, 5)
) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "period_start_utc": [r[0] for r in rows],
            "value": [r[1] for r in rows],
            "fetched_at_utc": [fetched] * len(rows),
        },
        schema=SCHEMA,
    )


JAN = [(datetime(2026, 1, 31, 23, 0), 1), (datetime(2026, 1, 31, 23, 30), 2)]
FEB = [(datetime(2026, 2, 1, 0, 0), 3)]


def test_upsert_writes_one_file_per_month(tmp_path: Path) -> None:
    s = store(tmp_path)
    stats = s.upsert(frame(JAN + FEB))
    assert (stats.inserted, stats.updated, stats.unchanged) == (3, 0, 0)
    assert [p.name for p in s.files()] == ["2026-01.parquet", "2026-02.parquet"]
    assert s.read().height == 3


def test_repeat_upsert_is_a_no_op(tmp_path: Path) -> None:
    s = store(tmp_path)
    s.upsert(frame(JAN + FEB))
    before = {p: p.read_bytes() for p in s.files()}
    stats = s.upsert(frame(JAN + FEB, fetched=datetime(2026, 1, 6)))
    assert (stats.inserted, stats.updated, stats.unchanged) == (0, 0, 3)
    assert stats.files_written == []
    assert {p: p.read_bytes() for p in s.files()} == before


def test_changed_values_replace_old_rows(tmp_path: Path) -> None:
    s = store(tmp_path)
    s.upsert(frame(JAN))
    stats = s.upsert(frame([(JAN[1][0], 20)], fetched=datetime(2026, 1, 6)))
    assert (stats.inserted, stats.updated) == (0, 1)
    data = s.read().sort("period_start_utc")
    assert data["value"].to_list() == [1, 20]
    assert data["fetched_at_utc"].to_list() == [datetime(2026, 1, 5), datetime(2026, 1, 6)]


def test_null_to_value_counts_as_update(tmp_path: Path) -> None:
    s = store(tmp_path)
    initial = frame(JAN).with_columns(pl.lit(None, dtype=pl.Int32()).alias("value"))
    s.upsert(initial)
    assert s.upsert(frame(JAN)).updated == 2


def test_last_duplicate_in_batch_wins(tmp_path: Path) -> None:
    s = store(tmp_path)
    s.upsert(frame([(JAN[0][0], 1), (JAN[0][0], 9)]))
    assert s.read()["value"].to_list() == [9]


def test_time_bounds_and_distinct_times(tmp_path: Path) -> None:
    s = store(tmp_path)
    assert s.time_bounds() == (None, None)
    s.upsert(frame(JAN + FEB))
    lo, hi = s.time_bounds()
    assert lo == datetime(2026, 1, 31, 23, 0, tzinfo=UTC)
    assert hi == datetime(2026, 2, 1, tzinfo=UTC)
    assert len(s.distinct_times()) == 3


def test_schema_mismatch_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SchemaMismatchError, match="unexpected"):
        store(tmp_path).upsert(frame(JAN).with_columns(pl.lit(1).alias("extra")))


def test_null_keys_are_rejected(tmp_path: Path) -> None:
    bad = frame(JAN).with_columns(pl.lit(None, dtype=pl.Datetime("us")).alias("period_start_utc"))
    with pytest.raises(SchemaMismatchError, match="null key"):
        store(tmp_path).upsert(bad)


def test_key_columns_must_exist(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not in the schema"):
        ParquetStore(tmp_path, SCHEMA, ["missing"], "period_start_utc")


def test_placeholder_is_removed_once_data_arrives(tmp_path: Path) -> None:
    s = store(tmp_path)
    write_placeholder(s)
    assert (s.root / PLACEHOLDER).exists()
    assert s.read().height == 0
    s.upsert(frame(JAN))
    assert not (s.root / PLACEHOLDER).exists()

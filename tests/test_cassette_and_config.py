from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from gridwatch.config import NATIONAL_EARLIEST, ConfigError, Settings
from gridwatch.ingest.cassette import CassetteMissError, RecordingTransport, ReplayTransport


def test_record_then_replay(tmp_path: Path) -> None:
    inner = httpx.MockTransport(
        lambda request: httpx.Response(
            200, json={"path": request.url.path}, headers={"ETag": '"x"', "Set-Cookie": "drop-me"}
        )
    )
    recorder = RecordingTransport(tmp_path, inner)
    with httpx.Client(transport=recorder) as client:
        assert client.get("https://api.test/a").json() == {"path": "/a"}
    replay = ReplayTransport(tmp_path)
    with httpx.Client(transport=replay) as client:
        response = client.get("https://api.test/a")
    assert response.json() == {"path": "/a"}
    assert response.headers["etag"] == '"x"'
    assert "set-cookie" not in response.headers
    assert replay.requests == ["https://api.test/a"]


def test_replay_miss_is_explicit(tmp_path: Path) -> None:
    recorder = RecordingTransport(tmp_path, httpx.MockTransport(lambda r: httpx.Response(204)))
    with httpx.Client(transport=recorder) as client:
        client.get("https://api.test/a")
    with (
        httpx.Client(transport=ReplayTransport(tmp_path)) as client,
        pytest.raises(CassetteMissError, match=r"no recording for https://api\.test/b"),
    ):
        client.get("https://api.test/b")


def test_empty_cassette_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(CassetteMissError, match="no recordings"):
        ReplayTransport(tmp_path)


def test_defaults(tmp_path: Path) -> None:
    settings = Settings.from_env({"GRIDWATCH_DATA_DIR": str(tmp_path)})
    assert settings.national_start == NATIONAL_EARLIEST
    assert settings.regional_start == datetime(2023, 1, 1, tzinfo=UTC)
    assert settings.refetch_days == 2
    assert settings.min_request_interval_s == 1.0
    assert settings.warehouse_path == tmp_path.resolve() / "warehouse" / "gridwatch.duckdb"
    assert settings.duckdb_memory == "2GB"


def test_overrides_are_parsed(tmp_path: Path) -> None:
    settings = Settings.from_env(
        {
            "GRIDWATCH_DATA_DIR": str(tmp_path),
            "GRIDWATCH_REGIONAL_START": "2024-06-01",
            "GRIDWATCH_REFETCH_DAYS": "5",
            "GRIDWATCH_MIN_REQUEST_INTERVAL": "0.5",
            "GRIDWATCH_DUCKDB_MEMORY": "1.5GiB",
        }
    )
    assert settings.regional_start == datetime(2024, 6, 1, tzinfo=UTC)
    assert settings.duckdb_memory == "1.5GiB"
    assert settings.refetch_days == 5
    assert settings.min_request_interval_s == 0.5


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("GRIDWATCH_REFETCH_DAYS", "two", "not a whole number"),
        ("GRIDWATCH_REFETCH_DAYS", "40", "between 0 and 31"),
        ("GRIDWATCH_MIN_REQUEST_INTERVAL", "fast", "not a number"),
        ("GRIDWATCH_HTTP_TIMEOUT", "0", "between 1.0 and 600.0"),
        ("GRIDWATCH_NATIONAL_START", "yesterday", "ISO 8601"),
        ("GRIDWATCH_NATIONAL_START", "2015-01-01", "earlier than the first period"),
        ("GRIDWATCH_DUCKDB_MEMORY", "lots", "not a memory size"),
        ("GRIDWATCH_DUCKDB_MEMORY", "2", "not a memory size"),
    ],
)
def test_invalid_values_name_the_variable(name: str, value: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message) as info:
        Settings.from_env({name: value})
    assert name in str(info.value)


def test_dbt_gets_the_validated_memory_limit(tmp_path: Path) -> None:
    from gridwatch.transform import dbt_environment

    settings = Settings.from_env(
        {"GRIDWATCH_DATA_DIR": str(tmp_path), "GRIDWATCH_DUCKDB_MEMORY": "512MB"}
    )
    assert dbt_environment(settings)["GRIDWATCH_DUCKDB_MEMORY"] == "512MB"

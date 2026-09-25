from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest

from gridwatch.ingest import carbon_intensity as ci
from tests.helpers import cassette_body

BASE = "https://api.carbonintensity.org.uk"
FETCHED = datetime(2026, 9, 20, 0, 5, tzinfo=UTC)


def utc(
    year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0
) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


def test_floor_and_ceil_half_hour() -> None:
    assert ci.floor_half_hour(utc(2026, 1, 1, 10, 44, 59)) == utc(2026, 1, 1, 10, 30)
    assert ci.ceil_half_hour(utc(2026, 1, 1, 10, 44)) == utc(2026, 1, 1, 11, 0)
    assert ci.ceil_half_hour(utc(2026, 1, 1, 10, 30)) == utc(2026, 1, 1, 10, 30)


def test_window_validation() -> None:
    with pytest.raises(ValueError, match="half-hour"):
        ci.Window(utc(2026, 1, 1, 0, 10), utc(2026, 1, 2))
    with pytest.raises(ValueError, match="after its start"):
        ci.Window(utc(2026, 1, 2), utc(2026, 1, 1))
    with pytest.raises(ValueError, match="timezone-aware"):
        ci.Window(datetime(2026, 1, 1), datetime(2026, 1, 2))


def test_chunks_respect_span_and_cover_window() -> None:
    window = ci.Window(utc(2026, 2, 1), utc(2026, 4, 15))
    chunks = list(ci.chunk_window(window, timedelta(days=30)))
    assert chunks[0].start == window.start
    assert chunks[-1].end == window.end
    assert all(c.end - c.start <= timedelta(days=30) for c in chunks)
    assert all(a.end == b.start for a, b in pairwise(chunks))


def test_chunks_split_just_before_new_year() -> None:
    window = ci.Window(utc(2025, 12, 20), utc(2026, 1, 10))
    chunks = list(ci.chunk_window(window, timedelta(days=30)))
    assert [c.end for c in chunks] == [utc(2025, 12, 31, 23, 30), utc(2026, 1, 10)]


def test_year_split_point_after_boundary() -> None:
    assert ci.year_split_point(utc(2025, 12, 31, 23, 30)) == utc(2026, 12, 31, 23, 30)
    assert ci.year_split_point(utc(2025, 6, 1)) == utc(2025, 12, 31, 23, 30)


def test_range_url_asks_for_half_hours_ending_in_window() -> None:
    window = ci.Window(utc(2026, 1, 1), utc(2026, 1, 2))
    assert ci.range_url(BASE, ci.Dataset.NATIONAL_INTENSITY, window) == (
        f"{BASE}/intensity/2026-01-01T00:30Z/2026-01-02T00:00Z"
    )
    assert ci.range_url(BASE, ci.Dataset.REGIONAL, window).startswith(f"{BASE}/regional/intensity/")


def test_single_half_hour_window_is_widened() -> None:
    window = ci.Window(utc(2026, 1, 1, 10), utc(2026, 1, 1, 10, 30))
    assert ci.range_url(BASE, ci.Dataset.GENERATION_MIX, window) == (
        f"{BASE}/generation/2026-01-01T10:30Z/2026-01-01T11:00Z"
    )


def test_plan_requests_uses_dataset_limits() -> None:
    window = ci.Window(utc(2026, 3, 1), utc(2026, 3, 31))
    assert len(ci.plan_requests(BASE, ci.Dataset.NATIONAL_INTENSITY, [window])) == 1
    assert len(ci.plan_requests(BASE, ci.Dataset.REGIONAL, [window])) == 3


def test_parse_national_recorded_response() -> None:
    payload = json.loads(cassette_body("/intensity/2026-08-22T00:30Z/"))
    window = ci.Window(utc(2026, 8, 22), utc(2026, 9, 20))
    frame = ci.parse_national(payload, FETCHED, window)
    assert frame.height == 29 * 48
    assert frame.schema == ci.NATIONAL_SCHEMA
    assert frame["period_start_utc"].min() == datetime(2026, 8, 22)
    assert frame["period_start_utc"].is_unique().all()
    assert set(frame["intensity_index"].unique()) <= ci.INTENSITY_INDEX_VALUES


def test_parse_generation_recorded_response() -> None:
    payload = json.loads(cassette_body("/generation/2026-08-22T00:30Z/"))
    frame = ci.parse_generation(payload, FETCHED, None)
    per_period = frame.group_by("period_start_utc").agg(total=frame["share_pct"].sum())
    assert frame["fuel"].n_unique() == 9
    assert per_period.height * 9 == frame.height


def test_parse_regional_recorded_response() -> None:
    payload = json.loads(cassette_body("/regional/intensity/"))
    intensity, mix = ci.parse_regional(payload, FETCHED, None)
    assert intensity["region_id"].n_unique() == 18
    assert mix.height == intensity.height * 9
    assert intensity.filter(intensity["region_id"] == 18)[
        "region_short_name"
    ].unique().to_list() == ["GB"]


def test_parse_snapshot_recorded_response() -> None:
    payload = json.loads(cassette_body("/fw48h"))
    issued = utc(2026, 9, 20)
    frame = ci.parse_snapshot(payload, issued, FETCHED)
    assert frame.height == 97
    assert frame["issued_at_utc"].unique().to_list() == [datetime(2026, 9, 20)]


def test_error_document_raises_api_error() -> None:
    payload = {"error": {"code": "400 Bad Request", "message": "range too long"}}
    with pytest.raises(ci.ApiError, match="range too long"):
        ci.parse_national(payload, FETCHED, None)


@pytest.mark.parametrize(
    ("record", "message"),
    [
        (
            {
                "from": "2026-01-01T00:00Z",
                "to": "2026-01-01T01:00Z",
                "intensity": {"forecast": 1, "actual": 1, "index": "low"},
            },
            "not 30 minutes",
        ),
        (
            {"from": "2026-01-01 00:00", "to": "2026-01-01T00:30Z", "intensity": {}},
            "timestamp format",
        ),
        (
            {
                "from": "2026-01-01T00:00Z",
                "to": "2026-01-01T00:30Z",
                "intensity": {"forecast": 1, "actual": 1, "index": "extreme"},
            },
            "unknown intensity",
        ),
        (
            {
                "from": "2026-01-01T00:00Z",
                "to": "2026-01-01T00:30Z",
                "intensity": {"forecast": "high", "actual": 1, "index": "low"},
            },
            "number or null",
        ),
        ({"from": "2026-01-01T00:00Z", "to": "2026-01-01T00:30Z"}, "no intensity"),
    ],
)
def test_malformed_national_records_fail_loudly(record: dict[str, object], message: str) -> None:
    with pytest.raises(ci.ParseError, match=message):
        ci.parse_national({"data": [record]}, FETCHED, None)


def test_non_object_payload() -> None:
    with pytest.raises(ci.ParseError, match="not a JSON object"):
        ci.parse_national([1, 2], FETCHED, None)


def test_windows_when_store_is_empty() -> None:
    windows = ci.windows_to_fetch(
        utc(2026, 1, 1), None, None, utc(2026, 1, 3, 10, 7), timedelta(days=2)
    )
    assert windows == [ci.Window(utc(2026, 1, 1), utc(2026, 1, 3, 10))]


def test_windows_refetch_recent_history() -> None:
    windows = ci.windows_to_fetch(
        utc(2026, 1, 1), utc(2026, 1, 1), utc(2026, 1, 10, 12), utc(2026, 1, 11), timedelta(days=2)
    )
    assert windows == [ci.Window(utc(2026, 1, 8, 12, 30), utc(2026, 1, 11))]


def test_windows_backfill_when_start_moves_earlier() -> None:
    windows = ci.windows_to_fetch(
        utc(2025, 12, 1), utc(2026, 1, 1), utc(2026, 1, 10), utc(2026, 1, 10, 1), timedelta(0)
    )
    assert windows[0] == ci.Window(utc(2025, 12, 1), utc(2026, 1, 1))
    assert windows[1] == ci.Window(utc(2026, 1, 10, 0, 30), utc(2026, 1, 10, 1))


def test_no_windows_when_start_is_in_future() -> None:
    assert ci.windows_to_fetch(utc(2030, 1, 1), None, None, utc(2026, 1, 1), timedelta(0)) == []


def test_internal_gaps() -> None:
    starts = [
        utc(2026, 1, 1, 0),
        utc(2026, 1, 1, 0, 30),
        utc(2026, 1, 1, 2),
        utc(2026, 1, 1, 2, 30),
    ]
    assert ci.internal_gaps(starts) == [ci.Window(utc(2026, 1, 1, 1), utc(2026, 1, 1, 2))]

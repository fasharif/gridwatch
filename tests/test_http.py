from __future__ import annotations

import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from gridwatch.ingest.http import (
    FetchError,
    HttpFetcher,
    RateLimiter,
    RetryPolicy,
    build_client,
    retry_after_seconds,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def test_rate_limiter_spaces_requests() -> None:
    clock = FakeClock()
    limiter = RateLimiter(1.0, clock=clock, sleep=clock.sleep)
    limiter.wait()
    clock.now += 0.25
    limiter.wait()
    limiter.wait()
    assert clock.slept == pytest.approx([0.75, 1.0])


def test_rate_limiter_rejects_negative_interval() -> None:
    with pytest.raises(ValueError, match="negative"):
        RateLimiter(-1)


def test_retry_delay_is_capped_and_jittered() -> None:
    policy = RetryPolicy(max_attempts=5, base_delay_s=2.0, max_delay_s=5.0)
    rng = random.Random(1)
    delays = [policy.delay(attempt, rng) for attempt in range(1, 6)]
    assert 1.0 <= delays[0] <= 2.0
    assert all(d <= 5.0 for d in delays)
    assert delays[-1] >= 2.5


@pytest.mark.parametrize(
    ("header", "expected"),
    [("7", 7.0), (None, None), ("soon", None)],
)
def test_retry_after_seconds(header: str | None, expected: float | None) -> None:
    assert retry_after_seconds(header) == expected


def test_retry_after_http_date() -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    header = format_datetime(now + timedelta(seconds=30), usegmt=True)
    assert retry_after_seconds(header, now=now.timestamp()) == pytest.approx(30.0)


def _sequence(*responses: httpx.Response | Exception) -> Callable[[httpx.Request], httpx.Response]:
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return handler


def test_retries_transient_errors_then_succeeds(
    fetcher_factory: Callable[..., HttpFetcher],
) -> None:
    handler = _sequence(
        httpx.ConnectError("boom"),
        httpx.Response(503, text="busy"),
        httpx.Response(200, json={"ok": True}),
    )
    sleeps: list[float] = []
    fetcher = fetcher_factory(httpx.MockTransport(handler), max_attempts=3, sleeps=sleeps)
    response = fetcher.get("https://example.test/x")
    assert response.json() == {"ok": True}
    assert fetcher.request_count == 3
    assert len(sleeps) == 2


def test_honours_retry_after_on_429(fetcher_factory: Callable[..., HttpFetcher]) -> None:
    handler = _sequence(
        httpx.Response(429, headers={"Retry-After": "3"}, text="slow down"),
        httpx.Response(200, text="ok"),
    )
    sleeps: list[float] = []
    fetcher = HttpFetcher(
        build_client("gridwatch-tests", 5.0, httpx.MockTransport(handler)),
        RateLimiter(0.0, sleep=sleeps.append),
        RetryPolicy(max_attempts=3, base_delay_s=0.01, max_delay_s=60.0),
        sleep=sleeps.append,
    )
    assert fetcher.get("https://example.test/x").text == "ok"
    assert max(sleeps) == pytest.approx(3.0)


def test_retry_after_is_capped(fetcher_factory: Callable[..., HttpFetcher]) -> None:
    handler = _sequence(
        httpx.Response(503, headers={"Retry-After": "86400"}, text="maintenance"),
        httpx.Response(200, text="ok"),
    )
    sleeps: list[float] = []
    fetcher = fetcher_factory(httpx.MockTransport(handler), sleeps=sleeps)
    fetcher.get("https://example.test/x")
    assert max(sleeps) <= 0.1  # five times the policy's maximum back-off


def test_client_errors_are_not_retried(fetcher_factory: Callable[..., HttpFetcher]) -> None:
    handler = _sequence(httpx.Response(404, text="missing"))
    fetcher = fetcher_factory(httpx.MockTransport(handler))
    with pytest.raises(FetchError, match="HTTP 404") as info:
        fetcher.get("https://example.test/x")
    assert info.value.status == 404
    assert fetcher.request_count == 1


def test_gives_up_after_max_attempts(fetcher_factory: Callable[..., HttpFetcher]) -> None:
    handler = _sequence(*(httpx.Response(502, text="bad gateway") for _ in range(3)))
    fetcher = fetcher_factory(httpx.MockTransport(handler), max_attempts=3)
    with pytest.raises(FetchError, match="gave up after 3 attempts"):
        fetcher.get("https://example.test/x")


def test_not_modified_is_returned(fetcher_factory: Callable[..., HttpFetcher]) -> None:
    handler = _sequence(httpx.Response(304))
    fetcher = fetcher_factory(httpx.MockTransport(handler))
    assert fetcher.get("https://example.test/x", {"If-None-Match": '"abc"'}).status_code == 304

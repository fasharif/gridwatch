"""HTTP fetching with a client-side rate limit and retries.

The Carbon Intensity API terms say the operator applies a rate limit and may block
clients that make many calls, but the limit is not published. The fetcher therefore
spaces requests out (one per second by default), retries transient failures with
exponential back-off and jitter, and honours ``Retry-After`` on 429 and 503 responses.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime

import httpx

log = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class FetchError(RuntimeError):
    """Raised when a request still fails after every retry, or fails permanently."""

    def __init__(self, url: str, reason: str, status: int | None = None) -> None:
        super().__init__(f"GET {url} failed: {reason}")
        self.url = url
        self.status = status


class RateLimiter:
    """Enforces a minimum interval between consecutive requests."""

    def __init__(
        self,
        min_interval_s: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if min_interval_s < 0:
            raise ValueError("min_interval_s must not be negative")
        self._min_interval = min_interval_s
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    def wait(self) -> None:
        """Block until the next request is allowed, then record it."""
        now = self._clock()
        if self._last is not None:
            remaining = self._min_interval - (now - self._last)
            if remaining > 0:
                self._sleep(remaining)
                now = self._clock()
        self._last = now


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential back-off with full jitter, capped at ``max_delay_s``."""

    max_attempts: int = 5
    base_delay_s: float = 1.0
    max_delay_s: float = 60.0
    retry_status: frozenset[int] = field(default=RETRYABLE_STATUS)

    def delay(self, attempt: int, rng: random.Random) -> float:
        """Delay before retry number ``attempt`` (1-based)."""
        ceiling = min(self.max_delay_s, self.base_delay_s * (2 ** (attempt - 1)))
        return rng.uniform(ceiling / 2, ceiling)


def retry_after_seconds(value: str | None, now: float | None = None) -> float | None:
    """Parse a ``Retry-After`` header given as seconds or as an HTTP date."""
    if value is None:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    current = time.time() if now is None else now
    return max(0.0, when.timestamp() - current)


class HttpFetcher:
    """GET requests with rate limiting and retries on transient failures."""

    def __init__(
        self,
        client: httpx.Client,
        limiter: RateLimiter,
        policy: RetryPolicy,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self._client = client
        self._limiter = limiter
        self._policy = policy
        self._sleep = sleep
        self._rng = rng or random.Random()
        self.request_count = 0

    def get(self, url: str, headers: Mapping[str, str] | None = None) -> httpx.Response:
        """Return the response for ``url``; 304 counts as success for conditional GETs."""
        last_reason = "no attempt made"
        last_status: int | None = None
        for attempt in range(1, self._policy.max_attempts + 1):
            self._limiter.wait()
            self.request_count += 1
            try:
                response = self._client.get(url, headers=dict(headers or {}))
            except httpx.TransportError as exc:
                last_reason = f"{type(exc).__name__}: {exc}"
                last_status = None
                retry_after = None
            else:
                if response.status_code < 400:
                    return response
                last_status = response.status_code
                last_reason = f"HTTP {response.status_code}: {_short_body(response)}"
                if response.status_code not in self._policy.retry_status:
                    raise FetchError(url, last_reason, last_status)
                retry_after = retry_after_seconds(response.headers.get("Retry-After"))
            if attempt == self._policy.max_attempts:
                break
            delay = self._policy.delay(attempt, self._rng)
            if retry_after is not None:
                delay = max(delay, min(retry_after, self._policy.max_delay_s * 5))
            log.warning(
                "attempt %d/%d for %s failed (%s); retrying in %.1fs",
                attempt,
                self._policy.max_attempts,
                url,
                last_reason,
                delay,
            )
            self._sleep(delay)
        raise FetchError(
            url, f"gave up after {self._policy.max_attempts} attempts ({last_reason})", last_status
        )


def _short_body(response: httpx.Response, limit: int = 200) -> str:
    try:
        text = response.text
    except (UnicodeDecodeError, httpx.ResponseNotRead):
        return "<unreadable body>"
    text = " ".join(text.split())
    return text[:limit] + ("..." if len(text) > limit else "")


def build_client(
    user_agent: str, timeout_s: float, transport: httpx.BaseTransport | None = None
) -> httpx.Client:
    """Create the shared HTTP client (a transport can be injected for tests and replays)."""
    return httpx.Client(
        headers={"User-Agent": user_agent, "Accept": "application/json, text/csv;q=0.9"},
        timeout=httpx.Timeout(timeout_s),
        follow_redirects=True,
        transport=transport,
    )

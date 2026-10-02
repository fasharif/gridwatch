from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from gridwatch.config import Settings
from gridwatch.ingest.http import HttpFetcher, RateLimiter, RetryPolicy, build_client
from tests.helpers import read_fixture_env


@pytest.fixture
def fixture_env() -> dict[str, str]:
    return read_fixture_env()


@pytest.fixture
def settings_factory(tmp_path: Path) -> Callable[..., Settings]:
    def make(**overrides: str) -> Settings:
        env = {"GRIDWATCH_DATA_DIR": str(tmp_path / "data"), "GRIDWATCH_MIN_REQUEST_INTERVAL": "0"}
        env.update(overrides)
        return Settings.from_env(env)

    return make


@pytest.fixture
def fetcher_factory() -> Callable[..., HttpFetcher]:
    """Build a fetcher around any transport, with no real sleeping."""

    def make(
        transport: httpx.BaseTransport,
        max_attempts: int = 3,
        sleeps: list[float] | None = None,
    ) -> HttpFetcher:
        record = sleeps if sleeps is not None else []
        return HttpFetcher(
            build_client("gridwatch-tests", 5.0, transport),
            RateLimiter(0.0, sleep=record.append),
            RetryPolicy(max_attempts=max_attempts, base_delay_s=0.01, max_delay_s=0.02),
            sleep=record.append,
        )

    return make

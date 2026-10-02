"""Shared test helpers (importable as tests.helpers)."""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from gridwatch.forecast.data import NationalSeries

FIXTURES = Path(__file__).parent / "fixtures"
CASSETTE = FIXTURES / "cassette"


def read_fixture_env() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (FIXTURES / "fixture.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def cassette_body(url_fragment: str) -> bytes:
    """Decompressed body of the single recorded response whose URL contains the fragment."""
    index = json.loads((CASSETTE / "index.json").read_text(encoding="utf-8"))
    matches = [entry for url, entry in index.items() if url_fragment in url]
    if len(matches) != 1:
        raise LookupError(f"{len(matches)} recordings match {url_fragment!r}")
    return gzip.decompress((CASSETTE / matches[0]["body"]).read_bytes())


def synthetic_series(
    days: int = 70, seed: int = 7, start: datetime | None = None
) -> NationalSeries:
    """A daily and weekly cycle with slow drift and noise, like a simplified GB series."""
    start = start or datetime(2025, 1, 1, tzinfo=UTC)
    n = days * 48
    t = np.arange(n)
    rng = np.random.default_rng(seed)
    daily = 40 * np.sin(2 * np.pi * t / 48)
    weekly = 15 * np.sin(2 * np.pi * t / (48 * 7))
    drift = 20 * np.sin(2 * np.pi * t / (48 * 45))
    actual = 150 + daily + weekly + drift + rng.normal(0, 8, n)
    api = actual + rng.normal(0, 5, n)
    return NationalSeries(start=start, actual=actual, api_forecast=api)


def autocorrelated_series(days: int = 70, seed: int = 3) -> NationalSeries:
    """A daily cycle plus a slowly wandering AR(1) level, so the next half-hour is close to
    the last one, as on the real grid. Persistence is hard to beat here in the first hour."""
    n = days * 48
    rng = np.random.default_rng(seed)
    level = np.zeros(n)
    for i in range(1, n):
        level[i] = 0.97 * level[i - 1] + rng.normal(0, 6)
    t = np.arange(n)
    actual = 150 + 40 * np.sin(2 * np.pi * t / 48) + level + rng.normal(0, 1.5, n)
    api = actual + rng.normal(0, 5, n)
    return NationalSeries(start=datetime(2025, 1, 1, tzinfo=UTC), actual=actual, api_forecast=api)

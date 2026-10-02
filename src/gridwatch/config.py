"""Runtime configuration read from environment variables.

Every setting has a documented default, so the pipeline runs with no configuration at all.
Values are validated up front so that a typo fails with a clear message instead of a
confusing error half-way through an ingestion run.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

CARBON_INTENSITY_API = "https://api.carbonintensity.org.uk"
EMBER_YEARLY_CSV = (
    "https://files.ember-energy.org/public-downloads/generation/outputs/"
    "release_generation_yearly_global.csv"
)
USER_AGENT = "gridwatch/0.1 (+https://github.com/fasharif/gridwatch)"

# Earliest periods the Carbon Intensity API serves, found by probing the API in September 2026.
NATIONAL_EARLIEST = datetime(2017, 9, 26, tzinfo=UTC)
GENERATION_EARLIEST = datetime(2018, 5, 10, 23, 30, tzinfo=UTC)
REGIONAL_EARLIEST = datetime(2018, 5, 10, 23, 30, tzinfo=UTC)

# A DuckDB memory_limit such as 2GB, 512MB or 1.5GiB.
MEMORY_SIZE = re.compile(r"^\d+(\.\d+)?\s*(KB|MB|GB|TB|KiB|MiB|GiB|TiB)$", re.IGNORECASE)


class ConfigError(ValueError):
    """Raised when an environment variable holds an invalid value."""


@dataclass(frozen=True)
class Settings:
    """Validated pipeline settings."""

    data_dir: Path
    national_start: datetime
    generation_start: datetime
    regional_start: datetime
    refetch_days: int
    min_request_interval_s: float
    max_attempts: int
    timeout_s: float
    duckdb_memory: str = "2GB"
    carbon_api_base: str = CARBON_INTENSITY_API
    ember_yearly_url: str = EMBER_YEARLY_CSV
    user_agent: str = USER_AGENT

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def warehouse_path(self) -> Path:
        return self.data_dir / "warehouse" / "gridwatch.duckdb"

    @property
    def outputs_dir(self) -> Path:
        return self.data_dir / "outputs"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Build settings from ``env`` (defaults to ``os.environ``)."""
        source = os.environ if env is None else env
        data_dir = Path(source.get("GRIDWATCH_DATA_DIR", "data")).expanduser().resolve()
        return cls(
            data_dir=data_dir,
            national_start=_date_setting(
                source, "GRIDWATCH_NATIONAL_START", NATIONAL_EARLIEST, NATIONAL_EARLIEST
            ),
            generation_start=_date_setting(
                source, "GRIDWATCH_GENERATION_START", GENERATION_EARLIEST, GENERATION_EARLIEST
            ),
            regional_start=_date_setting(
                source,
                "GRIDWATCH_REGIONAL_START",
                datetime(2023, 1, 1, tzinfo=UTC),
                REGIONAL_EARLIEST,
            ),
            refetch_days=_int_setting(source, "GRIDWATCH_REFETCH_DAYS", 2, minimum=0, maximum=31),
            min_request_interval_s=_float_setting(
                source, "GRIDWATCH_MIN_REQUEST_INTERVAL", 1.0, minimum=0.0, maximum=60.0
            ),
            max_attempts=_int_setting(source, "GRIDWATCH_MAX_ATTEMPTS", 5, minimum=1, maximum=20),
            timeout_s=_float_setting(
                source, "GRIDWATCH_HTTP_TIMEOUT", 60.0, minimum=1.0, maximum=600.0
            ),
            duckdb_memory=_memory_setting(source, "GRIDWATCH_DUCKDB_MEMORY", "2GB"),
        )


def _memory_setting(env: Mapping[str, str], name: str, default: str) -> str:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip()
    if not MEMORY_SIZE.match(value):
        raise ConfigError(f"{name}={raw!r} is not a memory size such as 2GB, 512MB or 1.5GiB")
    return value


def _date_setting(
    env: Mapping[str, str], name: str, default: datetime, earliest: datetime
) -> datetime:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        parsed = datetime.fromisoformat(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{name}={raw!r} is not an ISO 8601 date such as 2024-01-01") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    parsed = parsed.astimezone(UTC)
    if parsed < earliest:
        raise ConfigError(
            f"{name}={raw!r} is earlier than the first period the API serves "
            f"({earliest:%Y-%m-%d %H:%M} UTC)"
        )
    return parsed


def _int_setting(
    env: Mapping[str, str], name: str, default: int, *, minimum: int, maximum: int
) -> int:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name}={raw!r} is not a whole number") from exc
    if not minimum <= value <= maximum:
        raise ConfigError(f"{name}={value} must be between {minimum} and {maximum}")
    return value


def _float_setting(
    env: Mapping[str, str], name: str, default: float, *, minimum: float, maximum: float
) -> float:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name}={raw!r} is not a number") from exc
    if not minimum <= value <= maximum:
        raise ConfigError(f"{name}={value} must be between {minimum} and {maximum}")
    return value

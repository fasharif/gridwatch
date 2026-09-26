"""Time each pipeline step and write the table to docs/generated/.

Two workloads:

- ``fixture`` (default): the replayed cassette, a 30-day training window and a 7-day
  backtest. Fixed and offline, so results compare across machines. Writes
  docs/generated/timings.md.
- ``full``: a first run against the live API and Ember from an empty data directory: the
  full backfill since 2017, the dbt build over every regional row, the 48-hour forecast and
  the 365-day backtest with 14 retrains. Needs network access and takes long; it is the
  workload that decides whether the daily workflow fits its time limit. Writes
  docs/generated/timings-full.md.

Run it on an otherwise idle machine:

    uv run python scripts/time_pipeline.py --repeats 3
    uv run python scripts/time_pipeline.py --workload full
"""

from __future__ import annotations

import argparse
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    "fixture": ROOT / "docs" / "generated" / "timings.md",
    "full": ROOT / "docs" / "generated" / "timings-full.md",
}
DESCRIPTIONS = {
    "fixture": "the recorded fixture (59 days of GB data, 30-day training window, 7-day backtest)",
    "full": "a first run against the live API and Ember from an empty data directory",
}


def fixture_env() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (
        (ROOT / "tests" / "fixtures" / "fixture.env").read_text(encoding="utf-8").splitlines()
    ):
        line = line.strip()
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def steps(workload: str, env: dict[str, str], scratch: Path) -> list[tuple[str, list[str]]]:
    outputs = [
        ("report", ["report", "--out", str(scratch / "report.md")]),
        (
            "export",
            [
                "export",
                "--annual-csv",
                str(scratch / "annual.csv"),
                "--powerbi-dir",
                str(scratch / "site" / "downloads" / "powerbi"),
            ],
        ),
        ("docs (dbt docs generate)", ["docs", "--out", str(scratch / "site" / "dbt")]),
        (
            "site",
            ["site", "--out", str(scratch / "site"), "--annual-csv", str(scratch / "annual.csv")],
        ),
    ]
    if workload == "full":
        return [
            ("ingest (full backfill from the live API)", ["ingest"]),
            ("transform (dbt build and tests)", ["transform"]),
            ("forecast (train and predict)", ["forecast"]),
            ("backtest (365 origins, 14 retrains)", ["backtest"]),
            *outputs,
        ]
    as_of = env["FIXTURE_AS_OF"]
    return [
        (
            "ingest (replayed cassette)",
            ["ingest", "--replay", str(ROOT / "tests/fixtures/cassette"), "--as-of", as_of],
        ),
        ("transform (dbt build and tests)", ["transform", "--as-of", as_of]),
        ("forecast (train and predict)", ["forecast", "--train-days", "30"]),
        (
            "backtest (7 origins)",
            ["backtest", "--train-days", "30", "--test-days", "7", "--retrain-every", "7"],
        ),
        *outputs,
    ]


def run_once(workload: str) -> dict[str, float]:
    env_values = fixture_env() if workload == "fixture" else {}
    durations: dict[str, float] = {}
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        env = {**os.environ, **env_values, "GRIDWATCH_DATA_DIR": str(scratch / "data")}
        for name, args in steps(workload, env_values, scratch):
            start = time.perf_counter()
            subprocess.run(
                [sys.executable, "-m", "gridwatch.cli", *args],
                env=env,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            durations[name] = time.perf_counter() - start
    return durations


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--workload", choices=sorted(TARGETS), default="fixture")
    parser.add_argument("--repeats", type=int, default=None, help="default: 3 fixture, 1 full")
    args = parser.parse_args()
    repeats = args.repeats or (3 if args.workload == "fixture" else 1)
    if repeats < 1:
        parser.error("--repeats must be at least 1")
    runs = [run_once(args.workload) for _ in range(repeats)]
    lines = [
        f"# Pipeline timings: {args.workload} workload",
        "",
        f"Measured by `scripts/time_pipeline.py --workload {args.workload} --repeats {repeats}` "
        f"on {datetime.now(UTC):%Y-%m-%d} (UTC): {platform.platform()}, Python "
        f"{platform.python_version()}, {os.cpu_count()} logical CPUs. Median of {repeats} "
        f"run(s) on {DESCRIPTIONS[args.workload]}.",
        "",
        "| Step | Median seconds |",
        "| --- | ---: |",
    ]
    for name in runs[0]:
        lines.append(f"| {name} | {statistics.median(r[name] for r in runs):.1f} |")
    target = TARGETS[args.workload]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(target.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()

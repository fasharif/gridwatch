"""Time each pipeline step on the recorded fixture and write docs/generated/timings.md.

The workload is fixed (the replayed cassette, a 30-day training window and a 7-day
backtest), so results are comparable between machines. Run it on an otherwise idle machine:

    uv run python scripts/time_pipeline.py --repeats 3
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
TARGET = ROOT / "docs" / "generated" / "timings.md"


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


def steps(as_of: str, scratch: Path) -> list[tuple[str, list[str]]]:
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
        ("report", ["report", "--out", str(scratch / "report.md")]),
        (
            "export",
            [
                "export",
                "--annual-csv",
                str(scratch / "annual.csv"),
                "--powerbi-dir",
                str(scratch / "powerbi"),
            ],
        ),
        (
            "site",
            ["site", "--out", str(scratch / "site"), "--annual-csv", str(scratch / "annual.csv")],
        ),
    ]


def run_once(env_values: dict[str, str]) -> dict[str, float]:
    durations: dict[str, float] = {}
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        env = {**os.environ, **env_values, "GRIDWATCH_DATA_DIR": str(scratch / "data")}
        for name, args in steps(env_values["FIXTURE_AS_OF"], scratch):
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
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    runs = [run_once(fixture_env()) for _ in range(args.repeats)]
    lines = [
        "# Pipeline timings",
        "",
        f"Measured by `scripts/time_pipeline.py --repeats {args.repeats}` on "
        f"{datetime.now(UTC):%Y-%m-%d} (UTC): {platform.platform()}, Python "
        f"{platform.python_version()}, {os.cpu_count()} logical CPUs. Median of "
        f"{args.repeats} runs on the recorded fixture.",
        "",
        "| Step | Median seconds |",
        "| --- | ---: |",
    ]
    for name in runs[0]:
        lines.append(f"| {name} | {statistics.median(r[name] for r in runs):.1f} |")
    TARGET.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(TARGET.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()

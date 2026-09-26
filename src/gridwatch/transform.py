"""Run the dbt project against the raw Parquet data.

dbt runs in a child process. That keeps its DuckDB connection out of this process (DuckDB
refuses a second connection with different settings to the same file) and means a dbt
crash cannot leave the caller in a half-configured state. Results are read back from
dbt's run_results.json.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gridwatch.config import Settings


def _project_dir() -> Path:
    """The dbt project: packaged inside the wheel, or at the root of a source checkout."""
    packaged = Path(__file__).resolve().parent / "dbt_project"
    if (packaged / "dbt_project.yml").exists():
        return packaged
    return Path(__file__).resolve().parents[2] / "dbt"


PROJECT_DIR = _project_dir()
FAILED = frozenset({"error", "fail", "runtime error"})


class TransformError(RuntimeError):
    """dbt failed, or its inputs are missing."""


@dataclass(frozen=True)
class TransformResult:
    success: bool
    results: list[dict[str, Any]]

    @property
    def failures(self) -> list[dict[str, Any]]:
        return [r for r in self.results if r["status"] in FAILED]

    @property
    def warnings(self) -> list[dict[str, Any]]:
        return [r for r in self.results if r["status"] == "warn"]


def dbt_environment(settings: Settings) -> dict[str, str]:
    """Environment variables the dbt profile and sources read."""
    return {
        "GRIDWATCH_DATA_DIR": settings.data_dir.as_posix(),
        "GRIDWATCH_WAREHOUSE": settings.warehouse_path.as_posix(),
        "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
    }


def check_inputs(settings: Settings) -> None:
    ember = settings.raw_dir / "ember" / "yearly_electricity" / "yearly_electricity.parquet"
    national = settings.raw_dir / "carbon_intensity" / "national_intensity"
    missing = [str(p) for p in (ember, national) if not p.exists()]
    if missing:
        raise TransformError(
            "raw data not found: " + ", ".join(missing) + ". Run `gridwatch ingest` first."
        )


def parse_run_results(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        {
            "name": str(r["unique_id"]).split(".")[-1]
            if r["unique_id"].startswith("model.")
            else str(r["unique_id"]).split(".", 2)[-1],
            "unique_id": r["unique_id"],
            "status": str(r["status"]),
            "failures": r.get("failures"),
            "message": r.get("message"),
        }
        for r in data.get("results", [])
    ]


def run_dbt(
    settings: Settings,
    command: Sequence[str] = ("build",),
    variables: Mapping[str, object] | None = None,
    project_dir: Path = PROJECT_DIR,
) -> TransformResult:
    """Run a dbt command in a child process and summarise every node result."""
    check_inputs(settings)
    settings.warehouse_path.parent.mkdir(parents=True, exist_ok=True)
    target = settings.data_dir / "dbt-target"
    args = [
        sys.executable,
        "-m",
        "dbt.cli.main",
        *command,
        "--project-dir",
        str(project_dir),
        "--profiles-dir",
        str(project_dir),
        "--target-path",
        str(target),
        "--log-path",
        str(settings.data_dir / "dbt-logs"),
    ]
    if variables:
        args += ["--vars", json.dumps(dict(variables))]
    env = {**os.environ, **dbt_environment(settings)}
    run_results = target / "run_results.json"
    run_results.unlink(missing_ok=True)
    completed = subprocess.run(args, env=env, check=False)
    results = parse_run_results(run_results)
    if not results and completed.returncode != 0:
        raise TransformError(f"dbt {' '.join(command)} exited with code {completed.returncode}")
    return TransformResult(success=completed.returncode == 0, results=results)


def generate_docs(settings: Settings, out_dir: Path, project_dir: Path = PROJECT_DIR) -> Path:
    """Build the dbt docs (models, tests, lineage, exposures) as one static HTML page."""
    if not settings.warehouse_path.exists():
        raise TransformError(
            f"no warehouse at {settings.warehouse_path}; run `gridwatch transform` first"
        )
    result = run_dbt(settings, ("docs", "generate", "--static"), project_dir=project_dir)
    page = settings.data_dir / "dbt-target" / "static_index.html"
    if not result.success or not page.exists():
        raise TransformError("dbt docs generate failed; see the dbt log above")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "index.html"
    shutil.copyfile(page, target)
    return target

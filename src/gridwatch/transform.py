"""Run the dbt project against the raw Parquet data."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gridwatch.config import Settings

PROJECT_DIR = Path(__file__).resolve().parents[2] / "dbt"


class TransformError(RuntimeError):
    """dbt failed, or its inputs are missing."""


@dataclass(frozen=True)
class TransformResult:
    success: bool
    results: list[dict[str, Any]]

    @property
    def failures(self) -> list[dict[str, Any]]:
        return [r for r in self.results if r["status"] in {"error", "fail", "runtime error"}]

    @property
    def warnings(self) -> list[dict[str, Any]]:
        return [r for r in self.results if r["status"] == "warn"]


def dbt_environment(settings: Settings) -> dict[str, str]:
    """Environment variables the dbt profile and sources read."""
    return {
        "GRIDWATCH_DATA_DIR": settings.data_dir.as_posix(),
        "GRIDWATCH_WAREHOUSE": settings.warehouse_path.as_posix(),
    }


def check_inputs(settings: Settings) -> None:
    ember = settings.raw_dir / "ember" / "yearly_electricity" / "yearly_electricity.parquet"
    national = settings.raw_dir / "carbon_intensity" / "national_intensity"
    missing = [str(p) for p in (ember, national) if not p.exists()]
    if missing:
        raise TransformError(
            "raw data not found: " + ", ".join(missing) + ". Run `gridwatch ingest` first."
        )


def run_dbt(
    settings: Settings,
    command: Sequence[str] = ("build",),
    variables: Mapping[str, object] | None = None,
    project_dir: Path = PROJECT_DIR,
) -> TransformResult:
    """Invoke dbt in-process and return a summary of every node result."""
    from dbt.cli.main import dbtRunner

    check_inputs(settings)
    settings.warehouse_path.parent.mkdir(parents=True, exist_ok=True)
    os.environ.update(dbt_environment(settings))
    args = [
        *command,
        "--project-dir",
        str(project_dir),
        "--profiles-dir",
        str(project_dir),
    ]
    if variables:
        args += ["--vars", json.dumps(dict(variables))]
    outcome = dbtRunner().invoke(args)
    if outcome.exception is not None:
        raise TransformError(f"dbt {' '.join(command)} crashed: {outcome.exception}")
    results: list[dict[str, Any]] = []
    for node_result in getattr(outcome.result, "results", None) or []:
        results.append(
            {
                "name": node_result.node.name,
                "resource_type": str(node_result.node.resource_type),
                "status": str(node_result.status),
                "failures": node_result.failures,
                "message": node_result.message,
            }
        )
    return TransformResult(success=bool(outcome.success), results=results)

"""scripts/check_action_refs.py, with git ls-remote replaced by canned answers."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHA_TAG = "c18668ad3cf93ea998bef934396af7bb5c839dc7"
SHA_OBJECT = "d4ca2e3147b409459955613c152220f4db848ee1"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_action_refs", ROOT / "scripts" / "check_action_refs.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


refs_script = _load()

WORKFLOW = f"""
jobs:
  build:
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@{SHA_TAG} # v10.2.0
      - name: Restore
        uses: actions/cache/restore@v6
      - uses: ./local-action
      - uses: docker://alpine:3
      # uses: commented/out@v1
"""

# What GitHub answers: checkout has a v7 tag, cache a v6 branch, setup-uv a lightweight
# v10.2.0 tag and no v10; "annotated/action" has an annotated v1.0.0 tag.
REMOTE = {
    "actions/checkout": {"refs/tags/v7": "a" * 40},
    "actions/cache": {"refs/heads/v6": "b" * 40},
    "astral-sh/setup-uv": {"refs/tags/v10.2.0": SHA_TAG},
    "annotated/action": {
        "refs/tags/v1.0.0": SHA_OBJECT,
        "refs/tags/v1.0.0^{}": SHA_TAG,
    },
}


def fake_ls_remote(repo: str, patterns: list[str]) -> str:
    known = REMOTE.get(repo, {})
    return "".join(f"{known[p]}\t{p}\n" for p in patterns if p in known)


def test_parse_uses_skips_local_docker_and_comments() -> None:
    refs = refs_script.parse_uses(WORKFLOW, "ci.yml")
    assert [(r.repo, r.ref, r.comment) for r in refs] == [
        ("actions/checkout", "v7", None),
        ("astral-sh/setup-uv", SHA_TAG, "v10.2.0"),
        ("actions/cache", "v6", None),  # the sub-path /restore belongs to the same repo
    ]
    assert refs[0].line == 5


def test_every_reference_in_the_sample_resolves() -> None:
    refs = refs_script.parse_uses(WORKFLOW, "ci.yml")
    assert refs_script.check_all(refs, fake_ls_remote) == []


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        ("- uses: astral-sh/setup-uv@v10", "no tag or branch named v10"),
        (f"- uses: astral-sh/setup-uv@{'e' * 40} # v10.2.0", "not at the pinned commit"),
        (f"- uses: astral-sh/setup-uv@{SHA_TAG}", "needs a '# vX.Y.Z' comment"),
        (f"- uses: astral-sh/setup-uv@{SHA_TAG} # v99", "tag v99 does not exist"),
    ],
)
def test_broken_references_are_reported(line: str, reason: str) -> None:
    problems = refs_script.check_all(refs_script.parse_uses(line, "ci.yml"), fake_ls_remote)
    assert len(problems) == 1
    assert reason in problems[0]
    assert problems[0].startswith("ci.yml:1: astral-sh/setup-uv@")


def test_annotated_tag_is_compared_by_the_commit_it_points_at() -> None:
    good = refs_script.parse_uses(f"uses: annotated/action@{SHA_TAG} # v1.0.0", "w.yml")
    bad = refs_script.parse_uses(f"uses: annotated/action@{SHA_OBJECT} # v1.0.0", "w.yml")
    assert refs_script.check_all(good, fake_ls_remote) == []
    assert "not at the pinned commit" in refs_script.check_all(bad, fake_ls_remote)[0]


def test_unparseable_reference_fails_loudly() -> None:
    with pytest.raises(ValueError, match="cannot parse"):
        refs_script.parse_uses("- uses: checkout", "ci.yml")


def test_repository_workflows_pin_setup_uv_to_a_commit() -> None:
    """setup-uv publishes no moving major tag, so it must be pinned exactly."""
    for workflow in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        for ref in refs_script.parse_uses(workflow.read_text(encoding="utf-8"), workflow.name):
            if ref.repo == "astral-sh/setup-uv":
                assert refs_script.SHA.match(ref.ref), ref
                assert ref.comment is not None, ref
                assert ref.comment.startswith("v"), ref

"""Check that every action a workflow uses exists at the version it names.

actionlint checks workflow syntax and inputs but not whether ``owner/repo@ref`` exists: a
missing tag only fails when GitHub starts the job. This script asks GitHub with
``git ls-remote``, which needs no token and has no API rate limit.

- A tag or branch reference (``actions/checkout@v7``) must exist as a tag or branch.
- A commit SHA (``owner/repo@<40 hex> # v1.2.3``) must carry a version comment, and the tag
  named in the comment must point at exactly that commit, so the comment cannot drift.

Run from the repository root: ``uv run python scripts/check_action_refs.py``.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<ref>[^\s#]+)(?:\s+#\s*(?P<comment>\S+))?")
SHA = re.compile(r"^[0-9a-f]{40}$")

# ls-remote output: "<sha>\t<ref name>" per line.
LsRemote = Callable[[str, list[str]], str]


@dataclass(frozen=True)
class ActionRef:
    workflow: str
    line: int
    repo: str  # owner/name, without any sub-path
    ref: str
    comment: str | None

    def __str__(self) -> str:
        pinned = f"{self.repo}@{self.ref}" + (f" # {self.comment}" if self.comment else "")
        return f"{self.workflow}:{self.line}: {pinned}"


def parse_uses(text: str, workflow: str) -> list[ActionRef]:
    """Every remote ``uses:`` reference in a workflow file (local and docker ones skipped)."""
    refs: list[ActionRef] = []
    for number, line in enumerate(text.splitlines(), start=1):
        match = USES.match(line)
        if not match:
            continue
        target = match.group("ref").strip("'\"")
        if target.startswith(("./", "docker://")):
            continue
        path, _, ref = target.partition("@")
        parts = path.split("/")
        if not ref or len(parts) < 2:
            raise ValueError(f"{workflow}:{number}: cannot parse action reference {target!r}")
        refs.append(ActionRef(workflow, number, "/".join(parts[:2]), ref, match.group("comment")))
    return refs


def git_ls_remote(repo: str, patterns: list[str]) -> str:
    completed = subprocess.run(
        ["git", "ls-remote", f"https://github.com/{repo}", *patterns],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"git ls-remote {repo} failed: {completed.stderr.strip()}")
    return completed.stdout


def _refs(output: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in output.splitlines():
        sha, _, name = line.partition("\t")
        if sha and name:
            found[name.strip()] = sha.strip()
    return found


def check(ref: ActionRef, ls_remote: LsRemote = git_ls_remote) -> str | None:
    """None when the reference resolves, otherwise the reason it does not."""
    if SHA.match(ref.ref):
        if not ref.comment:
            return "a commit SHA needs a '# vX.Y.Z' comment naming its tag"
        tag = ref.comment
        found = _refs(ls_remote(ref.repo, [f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"]))
        # An annotated tag lists the tag object and, with ^{}, the commit it points at.
        commit = found.get(f"refs/tags/{tag}^{{}}") or found.get(f"refs/tags/{tag}")
        if commit is None:
            return f"tag {tag} does not exist"
        if commit != ref.ref:
            return f"tag {tag} points at {commit}, not at the pinned commit"
        return None
    found = _refs(ls_remote(ref.repo, [f"refs/tags/{ref.ref}", f"refs/heads/{ref.ref}"]))
    if not found:
        return f"no tag or branch named {ref.ref}"
    return None


def check_all(refs: Iterable[ActionRef], ls_remote: LsRemote = git_ls_remote) -> list[str]:
    problems: list[str] = []
    seen: dict[tuple[str, str, str | None], str | None] = {}
    for ref in refs:
        key = (ref.repo, ref.ref, ref.comment)
        if key not in seen:
            seen[key] = check(ref, ls_remote)
        if seen[key] is not None:
            problems.append(f"{ref}: {seen[key]}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("workflows", nargs="*", type=Path, help="default: .github/workflows/*")
    args = parser.parse_args(argv)
    files = args.workflows or sorted(Path(".github/workflows").glob("*.y*ml"))
    if not files:
        print("no workflow files found", file=sys.stderr)
        return 1
    refs = [r for f in files for r in parse_uses(f.read_text(encoding="utf-8"), f.as_posix())]
    problems = check_all(refs)
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        return 1
    print(f"{len(refs)} action references in {len(files)} workflow files all resolve")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

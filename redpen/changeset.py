"""Session changed-set: the single source of truth for what the agent changed.

Built once from two signals and reused by every session-scoping probe:
  1. the transcript's Write/Edit/MultiEdit tool-uses  -> provenance "transcript"
  2. the git delta (vs the baseline HEAD if present, else vs current HEAD plus
     working-tree status)                              -> provenance "git"

Bash file effects are captured by the git signal. Paths are normalized to
absolute strings so probes can query membership consistently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .baseline import _status_paths, read_baseline
from .util import run


def normalize(base: Path | str, path: str) -> str:
    """Canonical absolute path for ``path`` resolved against ``base``."""
    p = Path(path)
    if not p.is_absolute():
        p = Path(base) / p
    try:
        return str(p.resolve())
    except OSError:
        return str(p)


@dataclass
class ChangedSet:
    """Paths the agent created/modified this session, with provenance tags."""

    paths: dict[str, set[str]] = field(default_factory=dict)
    baseline_present: bool = False

    def contains(self, base: Path | str, path: str) -> bool:
        return normalize(base, path) in self.paths

    def provenance(self, base: Path | str, path: str) -> set[str]:
        return self.paths.get(normalize(base, path), set())

    def __bool__(self) -> bool:
        return bool(self.paths)


def _git_changed(project_root: Path, baseline: dict | None) -> set[str]:
    """Relative paths git considers changed -- since baseline if we have one."""
    paths: set[str] = set()
    head = (baseline or {}).get("head")
    if head:
        rc, out, _ = run(["git", "diff", "--name-only", head], cwd=project_root)
        if rc == 0:
            paths.update(p for p in out.splitlines() if p.strip())
        rc, out, _ = run(["git", "ls-files", "--others", "--exclude-standard"], cwd=project_root)
        if rc == 0:
            paths.update(p for p in out.splitlines() if p.strip())
    else:
        rc, out, _ = run(["git", "status", "--porcelain"], cwd=project_root)
        if rc == 0:
            paths.update(_status_paths(out))
    return paths


def build_changed_set(
    project_root: Path | str, transcript=None, baseline: dict | None = None
) -> ChangedSet:
    """Assemble the changed-set from the transcript and the git delta."""
    root = Path(project_root)
    if baseline is None:
        baseline = read_baseline(root)
    cs = ChangedSet(baseline_present=baseline is not None)

    def add(base: Path | str, rel: str, tag: str) -> None:
        cs.paths.setdefault(normalize(base, rel), set()).add(tag)

    if transcript is not None:
        tbase = transcript.cwd or root  # touched paths are relative to the session cwd
        for rel in transcript.touched_files:
            add(tbase, rel, "transcript")

    for rel in _git_changed(root, baseline):
        add(root, rel, "git")

    return cs

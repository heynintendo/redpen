"""Session changed-set: the single source of truth for what the agent changed.

Git is one optional evidence source, not a requirement. Signals, in priority:
  1. transcript Write/Edit/MultiEdit tool-uses -> provenance "transcript"  (PRIMARY; no git)
  2. filesystem delta vs the baseline snapshot  -> provenance "filesystem"  (no git)
  3. git delta (only when the folder is a repo) -> provenance "git"          (corroborating)

So "created X" / "modified X" resolve from the transcript and the filesystem
even with no repo. Paths are normalized to absolute strings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .baseline import _status_paths, read_baseline, walk_files
from .util import run


def is_git_repo(path: Path | str) -> bool:
    rc, out, _ = run(["git", "rev-parse", "--is-inside-work-tree"], cwd=path)
    return rc == 0 and out.strip() == "true"


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
    is_git: bool = False

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


def _fs_changed(project_root: Path, baseline: dict | None) -> set[str]:
    """Relative paths created/modified since the baseline snapshot (no git).

    Uses the baseline's per-file mtime snapshot when present (created = absent at
    baseline; modified = newer mtime); falls back to the baseline timestamp.
    Returns nothing without a baseline -- there's no reference point to diff.
    """
    if not baseline:
        return set()
    fs = baseline.get("fs")
    changed: set[str] = set()
    if isinstance(fs, dict):
        for rel, ap in walk_files(project_root):
            try:
                mtime = ap.stat().st_mtime
            except OSError:
                continue
            old = fs.get(rel)
            if old is None or mtime > float(old) + 1e-6:
                changed.add(rel)
        return changed
    ts = baseline.get("ts")
    if not ts:
        return set()
    try:
        base_t = datetime.fromisoformat(ts).timestamp()
    except (ValueError, TypeError):
        return set()
    for rel, ap in walk_files(project_root):
        try:
            if ap.stat().st_mtime >= base_t - 1.0:
                changed.add(rel)
        except OSError:
            continue
    return changed


def build_changed_set(
    project_root: Path | str, transcript=None, baseline: dict | None = None
) -> ChangedSet:
    """Assemble the changed-set from transcript + filesystem (+ git if a repo)."""
    root = Path(project_root)
    if baseline is None:
        baseline = read_baseline(root)
    repo = is_git_repo(root)
    cs = ChangedSet(baseline_present=baseline is not None, is_git=repo)

    def add(base: Path | str, rel: str, tag: str) -> None:
        cs.paths.setdefault(normalize(base, rel), set()).add(tag)

    # 1) PRIMARY: the transcript's own file-write tool-uses (needs no git).
    if transcript is not None:
        tbase = transcript.cwd or root  # touched paths are relative to the session cwd
        for rel in transcript.touched_files:
            add(tbase, rel, "transcript")

    # 2) filesystem delta vs the baseline (needs no git).
    for rel in _fs_changed(root, baseline):
        add(root, rel, "filesystem")

    # 3) git delta -- only when this actually is a repo (corroborating).
    if repo:
        for rel in _git_changed(root, baseline):
            add(root, rel, "git")

    return cs

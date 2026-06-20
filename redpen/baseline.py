"""Baseline snapshot written at task start: ``.redpen/baseline.json``.

Records the git HEAD, working-tree status, and content hashes of the files that
were already dirty at task start. With it, the session changed-set can diff
``baseline_head..now`` (catching changes the agent *committed* mid-session), and
TOCTOU is bounded (we know which files were already changed before the task).

Everything degrades gracefully: if there's no baseline, the changed-set falls
back to ``git status`` / diff-vs-HEAD, and probes never fail for its absence.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .config import LEDGER_DIR
from .util import run

BASELINE_FILE = "baseline.json"

# Dirs the filesystem snapshot skips (noise / caches), plus a hard file cap so a
# huge tree never blows up the snapshot.
_IGNORE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__",
    ".redpen", ".pytest_cache", "dist", "build", ".mypy_cache", ".ruff_cache",
    ".next", "target", ".tox", ".idea", ".gradle", ".cache",
}
_WALK_CAP = 5000


def walk_files(root: Path | str):
    """Yield (relpath, abspath) for files under root, skipping noise dirs, capped.

    This is the filesystem evidence source -- it needs no git, so created/
    modified-file claims resolve in a plain folder too.
    """
    root = Path(root)
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _IGNORE_DIRS and not d.startswith(".")]
        for fn in filenames:
            if count >= _WALK_CAP:
                return
            ap = Path(dirpath) / fn
            try:
                rel = str(ap.relative_to(root))
            except ValueError:
                continue
            count += 1
            yield rel, ap


def fs_snapshot(root: Path | str) -> dict[str, float]:
    """A {relpath: mtime} snapshot of the (bounded) working tree."""
    snap: dict[str, float] = {}
    for rel, ap in walk_files(root):
        try:
            snap[rel] = ap.stat().st_mtime
        except OSError:
            continue
    return snap


def baseline_path(project_root: Path | str) -> Path:
    return Path(project_root) / LEDGER_DIR / BASELINE_FILE


def _git(args: list[str], cwd: Path | str) -> str:
    rc, out, _ = run(["git", *args], cwd=cwd)
    return out if rc == 0 else ""


def _status_paths(porcelain: str) -> list[str]:
    paths: list[str] = []
    for ln in porcelain.splitlines():
        if not ln.strip():
            continue
        p = ln[3:]
        if " -> " in p:  # rename: take the new name
            p = p.split(" -> ", 1)[1]
        paths.append(p.strip().strip('"'))
    return paths


def _hash_file(p: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def write_baseline(project_root: Path | str, session_id: str = "") -> Path:
    """Snapshot HEAD + status + hashes of already-dirty files. Best-effort."""
    root = Path(project_root)
    status = _git(["status", "--porcelain"], root)
    hashes: dict[str, str] = {}
    for rel in _status_paths(status):
        digest = _hash_file(root / rel)
        if digest:
            hashes[rel] = digest
    data = {
        "head": _git(["rev-parse", "HEAD"], root).strip() or None,
        "branch": _git(["rev-parse", "--abbrev-ref", "HEAD"], root).strip() or None,
        "status": status,
        "hashes": hashes,
        # Filesystem snapshot (mtimes) -- the no-git evidence source for the
        # session changed-set.
        "fs": fs_snapshot(root),
        "session_id": session_id,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    path = baseline_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def read_baseline(project_root: Path | str) -> dict | None:
    """Load the baseline, or None if there isn't one / it's unreadable."""
    path = baseline_path(project_root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None

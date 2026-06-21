"""Concurrency soak: many `redpen check` runs in parallel across temp repos.

Shakes out races in the SQLite ledger, the baseline snapshot, last_run.json and
the judge cache. Real subprocesses (``python -m redpen``) give true concurrency.
Asserts: no corruption, no crashes, and no cross-contamination between projects.
"""

from __future__ import annotations

import json
import random
import sqlite3
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .cases import session, write_file, write_transcript


@dataclass
class SoakResult:
    n_projects: int = 0
    n_tasks: int = 0
    workers: int = 0
    exit_codes: list[int] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    hammer_rows: int = 0


def _run(project: Path, transcript: Path) -> int:
    proc = subprocess.run(
        [sys.executable, "-m", "redpen", "check", "--transcript", str(transcript), "--no-color", "--no-art"],
        cwd=project, capture_output=True, text=True, timeout=120,
    )
    return proc.returncode


def soak(n_projects: int = 24, hammer: int = 12, workers: int = 16, seed: int = 99) -> SoakResult:
    rng = random.Random(seed)
    root = Path(tempfile.mkdtemp(prefix="redpen-soak-"))
    tdir = root / "_t"
    tdir.mkdir()

    projects: list[tuple[Path, str, Path]] = []
    for i in range(n_projects):
        d = root / f"proj_{i}"
        d.mkdir()
        marker = f"uniq{i}x{rng.randrange(10**5):05d}"
        (d / f"{marker}.py").write_text("x = 1\n")
        tpath = tdir / f"{i}.jsonl"
        write_transcript(tpath, session(d, "do it", [write_file("t0", f"{marker}.py")], f"I created {marker}.py."))
        projects.append((d, marker, tpath))

    # one task per project, plus `hammer` extra runs all aimed at project 0 (to
    # stress concurrent writes to the SAME ledger / last_run / .redpen).
    tasks = [(d, t) for (d, _, t) in projects]
    tasks += [(projects[0][0], projects[0][2]) for _ in range(hammer)]
    rng.shuffle(tasks)

    res = SoakResult(n_projects=n_projects, n_tasks=len(tasks), workers=workers)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        res.exit_codes = list(ex.map(lambda a: _run(*a), tasks))

    for rc in res.exit_codes:
        if rc not in (0, 1):  # 0 = clean, 1 = a real FAIL; anything else is a crash
            res.problems.append(f"unexpected exit code {rc}")

    markers = [m for (_, m, _) in projects]
    for d, marker, _ in projects:
        led = d / ".redpen" / "ledger.db"
        if led.exists():
            try:
                con = sqlite3.connect(str(led))
                claims = " ".join(r[0] or "" for r in con.execute("SELECT claim FROM runs"))
                con.close()
            except sqlite3.DatabaseError as exc:
                res.problems.append(f"{d.name}: ledger corrupt ({exc})")
                continue
            for other in markers:
                if other != marker and other in claims:
                    res.problems.append(f"{d.name}: ledger leaked another project's claim ({other})")
            if d == projects[0][0]:
                res.hammer_rows = sum(1 for _ in claims.split() if marker in _) or claims.count(marker)
        lr = d / ".redpen" / "last_run.json"
        if lr.exists():
            try:
                json.loads(lr.read_text())
            except (json.JSONDecodeError, OSError) as exc:
                res.problems.append(f"{d.name}: last_run.json corrupt ({exc})")
    return res


def render_soak(res: SoakResult) -> str:
    ok = not res.problems
    lines = [
        "## Concurrency soak",
        "",
        f"- {res.n_tasks} parallel `redpen check` runs across {res.n_projects} repos "
        f"({res.workers} workers); project 0 hammered concurrently.",
        f"- crashes: {sum(1 for c in res.exit_codes if c not in (0, 1))}  ·  "
        f"corruption / cross-contamination: {len(res.problems)}",
        f"- result: {'clean — no corruption, no crashes, no cross-contamination' if ok else 'PROBLEMS'}",
    ]
    for p in res.problems[:30]:
        lines.append(f"  - {p}")
    return "\n".join(lines) + "\n"


def soak_summary(res: SoakResult) -> str:
    return (f"soak: tasks={res.n_tasks} projects={res.n_projects} "
            f"crashes={sum(1 for c in res.exit_codes if c not in (0, 1))} problems={len(res.problems)}")

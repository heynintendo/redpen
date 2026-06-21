"""Run a list of Cases, each in its own throwaway workspace, and collect results.

Used by both the report generator and quick iteration. Sequential by default
(so redpen's reported ``elapsed_seconds`` is contention-free for latency stats);
pass jobs>1 for faster correctness sweeps.
"""

from __future__ import annotations

import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .runner import run_case


def run_all(cases, *, jobs: int = 1, keep: bool = False, base=None, timeout: float = 90.0):
    base = Path(base) if base else Path(tempfile.mkdtemp(prefix="redpen_sh_"))
    base.mkdir(parents=True, exist_ok=True)

    def _one(item):
        idx, case = item
        # Index-prefixed dir guarantees uniqueness even on a case-insensitive
        # filesystem (where two case-only-different cids would otherwise collide).
        ws = base / f"{idx:04d}_{case.cid.replace('/', '__')}"
        try:
            return run_case(case, ws, timeout=timeout)
        finally:
            if not keep:
                shutil.rmtree(ws, ignore_errors=True)

    items = list(enumerate(cases))
    if jobs <= 1:
        results = [_one(it) for it in items]
    else:
        with ThreadPoolExecutor(max_workers=jobs) as ex:
            results = list(ex.map(_one, items))
    return results

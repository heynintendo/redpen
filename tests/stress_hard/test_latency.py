"""Latency: the deterministic path must stay sub-second, even on the 10k-file /
huge-transcript / sprawling-git cases. Measured via redpen's own reported
``elapsed_seconds`` (the engine's work), run sequentially for contention-free
numbers.
"""

from __future__ import annotations

import pytest

from cases.registry import all_cases
from harness.run_all import run_all

pytestmark = pytest.mark.slow


def _pct(values, p):
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


def test_deterministic_path_sub_second():
    cases = [c for c in all_cases() if not c.deep]
    by_cid = {c.cid: c for c in cases}
    results = run_all(cases, jobs=1)

    elapsed = [r.elapsed for r in results if r.elapsed is not None]
    p50, p95, p99, mx = _pct(elapsed, 50), _pct(elapsed, 95), _pct(elapsed, 99), max(elapsed)
    print(f"\ndeterministic elapsed: n={len(elapsed)} p50={p50:.3f}s "
          f"p95={p95:.3f}s p99={p99:.3f}s max={mx:.3f}s")

    assert p99 < 1.0, f"p99 deterministic elapsed {p99:.3f}s exceeds the 1s budget"

    # the explicitly-large cases must each be sub-second on their own
    for r in results:
        case = by_cid.get(r.cid)
        if case and "latency" in case.tags and r.elapsed is not None:
            assert r.elapsed < 1.0, f"{r.cid}: {r.elapsed:.3f}s on a large input exceeds 1s"


def test_no_case_hangs():
    """No case (including --deep) should take multiple seconds of wall time."""
    results = run_all(all_cases(), jobs=8)
    slow = [(r.cid, r.wall_ms) for r in results if (r.wall_ms or 0) > 8000]
    assert not slow, f"cases exceeded 8s wall time: {slow}"

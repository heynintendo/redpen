"""The deterministic path must stay sub-second even at the token / file ceiling."""

from __future__ import annotations

import pytest

from ucases.registry import all_cases
from uharness.run_all import run_all

pytestmark = pytest.mark.slow


def _pct(values, p):
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


def test_deterministic_path_sub_second():
    import os

    cases = [c for c in all_cases() if not c.deep]
    results = run_all(cases, jobs=1)
    elapsed = [r.elapsed for r in results if r.elapsed is not None]
    p50, p95, p99 = _pct(elapsed, 50), _pct(elapsed, 95), _pct(elapsed, 99)
    print(f"\nelapsed: n={len(elapsed)} p50={p50:.3f} p95={p95:.3f} p99={p99:.3f} max={max(elapsed):.3f}")

    # `elapsed_seconds` is redpen's own wall time, so it inflates when this test
    # runs under -n auto alongside many other subprocess-spawning workers. The
    # authoritative contention-free measurement is run_report.py (asserted strict
    # below only when running solo); under xdist load we assert the robust median.
    under_xdist = os.environ.get("PYTEST_XDIST_WORKER") is not None
    if under_xdist:
        assert p50 < 1.0, f"median deterministic elapsed {p50:.3f}s exceeds 1s even allowing for load"
    else:
        assert p99 < 1.0, f"p99 deterministic elapsed {p99:.3f}s exceeds 1s"

"""Per-case verdict regression for the ultra suite (dimensions A/B/D).

Every case has unarguable ground truth, so every case must match it -- a
divergence is either a real RedPen regression or a bad case. Robustness
invariants (no crash, exit 0/1, no hang) are checked alongside.

    REDPEN_STRESS_ULTRA=1 .venv/bin/python -m pytest tests/stress_ultra/test_stress_ultra.py -n auto
"""

from __future__ import annotations

import pytest

from ucases.registry import all_cases
from uharness.runner import run_case

_CASES = all_cases()


def _explain(res):
    bits = []
    for k in ("false_fail", "false_ok", "misparse", "soft"):
        for m in getattr(res, k):
            bits.append(f"{k}: {m}")
    bits.append("actual: " + ", ".join(f"{f['probe']}={f['verdict']}" for f in res.actual))
    return "\n".join(bits)


@pytest.mark.parametrize("case", _CASES, ids=[c.cid for c in _CASES])
def test_case(case, tmp_path):
    res = run_case(case, tmp_path)
    # Robustness + correctness only. A true hang is caught by the runner's 90s
    # subprocess timeout; contention-free latency is asserted by test_latency /
    # run_report (per-case `elapsed` inflates under -n auto CPU load).
    assert not res.error, f"{case.cid}: {res.error}"
    assert res.exit_code in (0, 1), f"{case.cid}: unexpected exit {res.exit_code}"
    assert res.passed, f"{case.cid} diverged from ground truth:\n{_explain(res)}"

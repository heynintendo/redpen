"""Per-case verdict + robustness tests, parametrized over the whole suite.

Run under pytest-xdist for a concurrency sweep across distinct temp repos:

    REDPEN_STRESS_HARD=1 .venv/bin/python -m pytest tests/stress_hard/test_stress_hard.py -n auto

Two layers per case:
  * robustness invariants that must ALWAYS hold (no crash, valid output, exit
    0/1, no multi-second hang);
  * verdict == ground truth, EXCEPT for the recorded known-divergence cases
    (the genuine findings), which must still diverge -- if one starts passing,
    update known_findings.py. New divergences fail the suite (regression guard).
"""

from __future__ import annotations

import pytest

from cases.registry import all_cases
from harness.runner import run_case
from known_findings import KNOWN_DIVERGENCES

_CASES = all_cases()
_ROBUSTNESS_BUDGET = 5.0  # generous; the strict sub-second check is in test_latency.py


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

    # --- robustness: must always hold -------------------------------------
    assert not res.error, f"{case.cid}: harness/redpen error: {res.error}"
    assert res.exit_code in (0, 1), f"{case.cid}: unexpected exit {res.exit_code}"
    if res.elapsed is not None:
        assert res.elapsed < _ROBUSTNESS_BUDGET, (
            f"{case.cid}: deterministic path took {res.elapsed}s (possible hang)")

    # --- verdict vs ground truth, pinned against known findings -----------
    if case.cid in KNOWN_DIVERGENCES:
        category, _root = KNOWN_DIVERGENCES[case.cid]
        assert not res.passed, (
            f"{case.cid}: known {category} finding now matches ground truth -- "
            f"remove it from known_findings.py\n{_explain(res)}")
    else:
        assert res.passed, f"{case.cid} diverged from ground truth:\n{_explain(res)}"

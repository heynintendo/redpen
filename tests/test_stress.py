"""Run a representative slice of the stress harness as part of the test suite.

The full 320-case fuzzer + soak run via `python -m tests.stress`; here we run a
smaller seeded slice so `pytest` enforces the two unforgivable invariants
(zero false FAIL, zero false OK) and that the fast path stays sub-second.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))  # repo root for tests.stress

import pytest  # noqa: E402

from tests.stress.harness import _pct, run_all  # noqa: E402
from tests.stress.soak import soak  # noqa: E402


@pytest.fixture(scope="module")
def fuzz_report():
    return run_all(n=108, seed=7)  # one run shared by the checks below


def test_fuzzer_has_no_false_fail_or_false_ok(fuzz_report):
    rep = fuzz_report
    false_fail = [f"{i.case}:{i.match}" for i in rep.issues if i.kind == "false_fail"]
    false_ok = [f"{i.case}:{i.match}" for i in rep.issues if i.kind == "false_ok"]
    assert false_fail == [], f"false FAIL (true claim marked FAIL): {false_fail}"
    assert false_ok == [], f"false OK (lie marked OK): {false_ok}"
    assert rep.counts["leak"] == 0, "a non-claim produced a verdict line"
    assert rep.errors == [], [e.detail for e in rep.errors]


def test_fuzzer_fast_path_is_sub_second(fuzz_report):
    assert _pct(fuzz_report.times, 0.99) < 1.0  # p99 under load stays sub-second


def test_concurrency_soak_no_corruption_or_contamination():
    res = soak(n_projects=10, hammer=6, workers=10)
    assert res.problems == [], res.problems
    assert all(c in (0, 1) for c in res.exit_codes), res.exit_codes

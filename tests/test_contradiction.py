"""Tests for the contradiction engine (the highest-precision FAIL)."""

from __future__ import annotations

import redpen.probes.run_probes as rp
from redpen.contradiction import find_failures
from redpen.probes.base import ProbeContext, Verdict
from redpen.probes.transcript_probes import contradiction_scan
from redpen.transcript import ToolEvent, Transcript


def _ev(output="", failed=False, command="pytest -q", label="pytest -q"):
    return ToolEvent(tool="Bash", label=label, failed=failed, command=command, output=output)


def test_output_signature_catches_masked_test_failure():
    # Exit was masked (failed=False, e.g. `pytest || true`), but the output
    # itself shows the failure -> still caught, and the line is quoted.
    t = Transcript(
        tool_events=[_ev(output="test_x.py::test_a FAILED\n=== 1 failed, 2 passed in 0.1s ===", failed=False)],
        final_assistant_text="All done, the tests pass.",
    )
    res = contradiction_scan(ProbeContext(cwd=".", transcript=t), kind="tests")
    assert res.verdict is Verdict.FAIL
    assert "1 failed" in res.evidence["contradiction"]


def test_build_error_signature_with_build_claim():
    t = Transcript(
        tool_events=[_ev(output="src/app.ts(3,5): error TS2322: Type 'x'.", command="tsc", label="tsc")],
        final_assistant_text="The build succeeds now.",
    )
    res = contradiction_scan(ProbeContext(cwd=".", transcript=t), kind="build")
    assert res.verdict is Verdict.FAIL
    assert "TS2322" in res.evidence["contradiction"]


def test_clean_session_is_ok():
    t = Transcript(
        tool_events=[_ev(output="3 passed in 0.1s", failed=False)],
        final_assistant_text="Done, tests pass.",
    )
    res = contradiction_scan(ProbeContext(cwd=".", transcript=t), kind="tests")
    assert res.verdict is Verdict.OK


def test_signature_without_success_narration_is_unverifiable():
    t = Transcript(
        tool_events=[_ev(output="=== 1 failed ===", failed=True)],
        final_assistant_text="Still debugging the failing test.",
    )
    res = contradiction_scan(ProbeContext(cwd=".", transcript=t), kind="tests")
    assert res.verdict is Verdict.UNVERIFIABLE


def test_irrelevant_failed_command_not_a_test_contradiction():
    # An `ls` that failed has nothing to do with a "tests pass" claim.
    t = Transcript(
        tool_events=[_ev(output="No such file", failed=True, command="ls /nope", label="ls /nope")],
        final_assistant_text="Done, tests pass.",
    )
    res = contradiction_scan(ProbeContext(cwd=".", transcript=t), kind="tests")
    assert res.verdict is Verdict.OK  # not relevant to tests -> no contradiction


def test_find_failures_unit_generic_traceback():
    events = [_ev(output="Traceback (most recent call last):\n  ...\nValueError: boom", failed=True)]
    fails = find_failures(events, kind="any")
    assert fails and "Traceback" in fails[0].line


def test_tests_pass_fails_on_transcript_output(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    (tmp_path / "tests").mkdir()
    t = Transcript(
        tool_events=[_ev(output="=== 2 failed, 1 passed in 0.3s ===", failed=False)],
        final_assistant_text="tests pass",
    )
    res = rp.tests_pass(ProbeContext(cwd=tmp_path, transcript=t))
    assert res.verdict is Verdict.FAIL
    assert "2 failed" in res.evidence.get("contradiction", "")

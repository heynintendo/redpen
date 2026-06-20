"""Edge-case hardening for the remaining probes.

The precision rule is absolute: absent/ambiguous evidence -> UNVERIFIABLE;
FAIL only on a genuine contradiction. Subprocess-dependent paths are mocked.
"""

from __future__ import annotations

import redpen.probes.gh_probes as gp
import redpen.probes.run_probes as rp
from redpen.claims import extract_claims
from redpen.probes import (
    branch_synced,
    build_ok,
    exit_code_scan,
    lint_clean,
    pr_status,
    unmapped,
)

# tests_pass is called as rp.tests_pass below: a bare module-level `tests_pass`
# name would be collected by pytest as a test (it matches the `test*` pattern).
from redpen.probes.base import ProbeContext, Verdict
from redpen.transcript import ToolEvent, Transcript
from redpen.util import RC_NOT_FOUND, RC_TIMEOUT


def _pytest_project(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    (tmp_path / "tests").mkdir()
    return tmp_path


# --- tests_pass -------------------------------------------------------------


def test_tests_pass_no_runner_is_unverifiable(tmp_path):
    res = rp.tests_pass(ProbeContext(cwd=tmp_path))
    assert res.verdict is Verdict.UNVERIFIABLE


def test_tests_pass_pytest_exit5_is_unverifiable_not_ok(tmp_path, monkeypatch):
    proj = _pytest_project(tmp_path)
    monkeypatch.setattr(rp, "run", lambda *a, **k: (5, "no tests ran", ""))
    res = rp.tests_pass(ProbeContext(cwd=proj, run=True))
    assert res.verdict is Verdict.UNVERIFIABLE
    assert "collect" in res.detail.lower()


def test_tests_pass_timeout_is_unverifiable(tmp_path, monkeypatch):
    proj = _pytest_project(tmp_path)
    monkeypatch.setattr(rp, "run", lambda *a, **k: (RC_TIMEOUT, "", "timeout"))
    res = rp.tests_pass(ProbeContext(cwd=proj, run=True))
    assert res.verdict is Verdict.UNVERIFIABLE
    assert "timed out" in res.detail.lower()


def test_tests_pass_executable_missing_is_unverifiable(tmp_path, monkeypatch):
    proj = _pytest_project(tmp_path)
    monkeypatch.setattr(rp, "run", lambda *a, **k: (RC_NOT_FOUND, "", "not found"))
    res = rp.tests_pass(ProbeContext(cwd=proj, run=True))
    assert res.verdict is Verdict.UNVERIFIABLE


def test_tests_pass_never_run_this_session_is_unverifiable(tmp_path):
    proj = _pytest_project(tmp_path)
    res = rp.tests_pass(ProbeContext(cwd=proj, transcript=Transcript(tool_events=[])))
    assert res.verdict is Verdict.UNVERIFIABLE
    assert "not run" in res.detail.lower()


def test_tests_pass_run_failure_is_fail(tmp_path, monkeypatch):
    proj = _pytest_project(tmp_path)
    monkeypatch.setattr(rp, "run", lambda *a, **k: (1, "", "1 failed"))
    res = rp.tests_pass(ProbeContext(cwd=proj, run=True))
    assert res.verdict is Verdict.FAIL


def test_tests_pass_multi_runner_note(tmp_path, monkeypatch):
    proj = _pytest_project(tmp_path)
    (proj / "package.json").write_text('{"scripts": {"test": "jest"}}')
    monkeypatch.setattr(rp, "run", lambda *a, **k: (0, "", ""))
    res = rp.tests_pass(ProbeContext(cwd=proj, run=True))
    assert res.verdict is Verdict.OK
    assert len(res.evidence.get("runners_detected", [])) > 1


# --- build_ok / lint_clean: tool absent/unconfigured -> UNVERIFIABLE --------


def test_build_ok_no_command_is_unverifiable(tmp_path):
    assert build_ok(ProbeContext(cwd=tmp_path, run=True)).verdict is Verdict.UNVERIFIABLE


def test_lint_clean_no_command_is_unverifiable(tmp_path):
    assert lint_clean(ProbeContext(cwd=tmp_path, run=True)).verdict is Verdict.UNVERIFIABLE


def test_lint_tool_not_installed_is_unverifiable(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n")
    monkeypatch.setattr(rp, "run", lambda *a, **k: (RC_NOT_FOUND, "", "not found"))
    res = lint_clean(ProbeContext(cwd=tmp_path, run=True))
    assert res.verdict is Verdict.UNVERIFIABLE


def test_lint_actual_errors_is_fail(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n")
    monkeypatch.setattr(rp, "run", lambda *a, **k: (1, "E501 line too long", ""))
    res = lint_clean(ProbeContext(cwd=tmp_path, run=True))
    assert res.verdict is Verdict.FAIL


# --- branch_synced / pr_status: network/tool problems -> UNVERIFIABLE -------


def test_branch_synced_no_upstream_is_unverifiable(git_repo, commit):
    commit(git_repo)
    assert branch_synced(ProbeContext(cwd=git_repo)).verdict is Verdict.UNVERIFIABLE


def test_branch_synced_unreachable_remote_is_unverifiable(git_repo, commit, git, tmp_path):
    commit(git_repo)
    git("remote", "add", "origin", str(tmp_path / "nonexistent.git"), cwd=git_repo)
    git("config", "branch.main.remote", "origin", cwd=git_repo)
    git("config", "branch.main.merge", "refs/heads/main", cwd=git_repo)

    res = branch_synced(ProbeContext(cwd=git_repo))
    assert res.verdict is Verdict.UNVERIFIABLE  # can't reach remote != contradiction


def test_pr_status_gh_missing_is_unverifiable(monkeypatch, tmp_path):
    monkeypatch.setattr(gp.shutil, "which", lambda name: None)
    res = pr_status(ProbeContext(cwd=tmp_path))
    assert res.verdict is Verdict.UNVERIFIABLE
    assert "gh" in res.detail.lower()


def test_pr_status_unauthenticated_is_unverifiable(monkeypatch, tmp_path):
    monkeypatch.setattr(gp.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(gp, "run", lambda *a, **k: (1, "", "not logged in"))
    res = pr_status(ProbeContext(cwd=tmp_path))
    assert res.verdict is Verdict.UNVERIFIABLE
    assert "auth" in res.detail.lower()


# --- exit_code_scan: never infer FAIL from a missing exit code --------------


def test_exit_code_scan_ignores_results_without_failure_signal():
    t = Transcript(
        tool_events=[
            ToolEvent(tool="Write", label="Write(x.py)", failed=False),  # non-shell, no exit code
            ToolEvent(tool="Bash", label="ls", failed=False),
        ],
        final_assistant_text="All done, everything works.",
    )
    res = exit_code_scan(ProbeContext(cwd=".", transcript=t))
    assert res.verdict is Verdict.OK  # no failure detected -> never FAIL


# --- catch-all: a claim with no probe -> labelled UNVERIFIABLE, not dropped --


def test_unmapped_probe_is_unverifiable():
    res = unmapped(ProbeContext(cwd="."))
    assert res.verdict is Verdict.UNVERIFIABLE
    assert "no probe" in res.detail.lower()


def test_claimlike_sentence_without_probe_maps_to_unmapped():
    claims = extract_claims("I refactored the authentication module.", source="transcript")
    assert len(claims) == 1
    assert claims[0].probe_specs[0].name == "unmapped"


def test_adhoc_unmappable_question_maps_to_unmapped():
    claims = extract_claims("did you frobnicate the widget", source="adhoc")
    assert claims and claims[0].probe_specs[0].name == "unmapped"


def test_filebacked_claim_still_uses_real_probe_not_unmapped():
    # A claim that DOES name a checkable target must not fall through to unmapped.
    claims = extract_claims("I created src/app.py", source="transcript")
    assert claims[0].probe_specs[0].name == "file_present"


# --- exit code: 1 only when a real FAIL exists ------------------------------


def test_exit_code_is_zero_without_a_real_fail(monkeypatch, tmp_path):
    from redpen.cli import main

    monkeypatch.chdir(tmp_path)  # empty, non-git dir -> nothing can FAIL
    rc = main(["check", "I refactored the auth module", "--no-art", "--no-color"])
    assert rc == 0  # only an UNVERIFIABLE (unmapped) -> exit 0, never 1


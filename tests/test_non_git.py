"""RedPen works fully in non-git folders: git is one optional evidence source."""

from __future__ import annotations

import json

from redpen.baseline import write_baseline
from redpen.changeset import build_changed_set, is_git_repo
from redpen.claims import drop_inapplicable_git_probes, extract_claims
from redpen.cli import main
from redpen.probes import file_present, git_pushed
from redpen.probes.base import ProbeContext, Verdict


class _T:  # minimal transcript stand-in for build_changed_set
    def __init__(self, cwd, touched):
        self.cwd = str(cwd)
        self.touched_files = list(touched)


# --- changed-set without git ------------------------------------------------


def test_non_git_folder_detected(tmp_path):
    assert is_git_repo(tmp_path) is False


def test_changeset_transcript_is_primary_no_git(tmp_path):
    (tmp_path / "app.py").write_text("print('hi')\n")
    cs = build_changed_set(tmp_path, transcript=_T(tmp_path, ["app.py"]))
    assert cs.contains(tmp_path, "app.py")
    assert "transcript" in cs.provenance(tmp_path, "app.py")
    assert cs.is_git is False


def test_changeset_filesystem_fallback_no_git(tmp_path):
    # Snapshot a baseline, then create a file -> filesystem signal detects it.
    (tmp_path / "old.py").write_text("old\n")
    write_baseline(tmp_path)  # records fs snapshot (no git)
    import time

    time.sleep(0.01)
    (tmp_path / "new.py").write_text("brand new\n")

    cs = build_changed_set(tmp_path)  # no transcript at all
    assert cs.contains(tmp_path, "new.py")
    assert "filesystem" in cs.provenance(tmp_path, "new.py")
    assert not cs.contains(tmp_path, "old.py")  # unchanged since baseline


def test_tests_pass_from_transcript_without_project_files(tmp_path):
    # A bare folder with no pytest config: the test run in the transcript is the
    # evidence, so "tests pass" resolves to OK (not "no command detected").
    import redpen.probes.run_probes as rp
    from redpen.transcript import ToolEvent, Transcript

    t = Transcript(tool_events=[
        ToolEvent(tool="Bash", label="pytest -q", command="pytest -q", failed=False, output="3 passed in 0.1s"),
    ])
    res = rp.tests_pass(ProbeContext(cwd=tmp_path, transcript=t))
    assert res.verdict is Verdict.OK


def test_created_claim_resolves_ok_via_transcript_no_git(tmp_path):
    (tmp_path / "app.py").write_text("x\n")
    cs = build_changed_set(tmp_path, transcript=_T(tmp_path, ["app.py"]))
    res = file_present(ProbeContext(cwd=tmp_path, changed_set=cs), path="app.py", created=True)
    assert res.verdict is Verdict.OK  # not UNVERIFIABLE, no git needed


# --- git-probe omission vs contradiction ------------------------------------


def test_done_claim_omits_git_probes_in_non_git():
    claims = extract_claims("Everything is done.", source="adhoc")
    claims = drop_inapplicable_git_probes(claims, is_git=False)
    names = {s.name for c in claims for s in c.probe_specs}
    assert "git_pushed" not in names and "git_clean" not in names  # omitted, no noise
    assert "tests_pass" in names and "contradiction_scan" in names  # non-git probes stay


def test_done_claim_keeps_git_probes_in_repo():
    claims = extract_claims("Everything is done.", source="adhoc")
    claims = drop_inapplicable_git_probes(claims, is_git=True)
    names = {s.name for c in claims for s in c.probe_specs}
    assert "git_pushed" in names and "git_clean" in names


def test_explicit_push_claim_kept_in_non_git_and_fails(tmp_path):
    claims = extract_claims("I pushed to origin.", source="adhoc")
    claims = drop_inapplicable_git_probes(claims, is_git=False)
    names = {s.name for c in claims for s in c.probe_specs}
    assert "git_pushed" in names  # explicit claim kept (not omitted)

    res = git_pushed(ProbeContext(cwd=tmp_path))  # non-git folder
    assert res.verdict is Verdict.FAIL
    assert "not a git repository" in res.detail


# --- non-claim skipping -----------------------------------------------------


def test_non_claims_are_skipped():
    assert extract_claims("I generated zero files.", source="transcript") == []
    assert extract_claims("Nothing to report. Fresh session.", source="transcript") == []
    assert extract_claims("I did not create any files.", source="transcript") == []


def test_real_claim_survives_alongside_non_claim():
    claims = extract_claims("I created config.py. Nothing else to do.", source="transcript")
    subjects = [s for c in claims for s in c.probe_specs]
    assert len(claims) == 1 and subjects[0].name == "file_present"


def test_positive_no_errors_is_not_skipped():
    # "no type errors" is a positive typecheck claim, not a non-claim.
    claims = extract_claims("mypy passes with no type errors.", source="transcript")
    names = {s.name for c in claims for s in c.probe_specs}
    assert "typecheck_clean" in names


# --- end-to-end CLI smoke in a non-git folder -------------------------------


def _transcript(path, cwd, final_text, touched):
    lines = [{"type": "user", "sessionId": "s", "cwd": str(cwd), "entrypoint": "cli",
              "message": {"role": "user", "content": "do it"}}]
    content = [{"type": "text", "text": "working"}]
    for i, f in enumerate(touched):
        content.append({"type": "tool_use", "id": f"t{i}", "name": "Write",
                        "input": {"file_path": f, "content": "x"}})
    lines.append({"type": "assistant", "sessionId": "s", "cwd": str(cwd), "entrypoint": "cli",
                  "message": {"role": "assistant", "content": content}})
    for i, f in enumerate(touched):
        lines.append({"type": "user", "sessionId": "s", "cwd": str(cwd), "entrypoint": "cli",
                      "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": "ok"}]},
                      "toolUseResult": {"success": True, "commandName": "Write"}})
    lines.append({"type": "assistant", "sessionId": "s", "cwd": str(cwd), "entrypoint": "cli",
                  "message": {"role": "assistant", "content": [{"type": "text", "text": final_text}]}})
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")


def test_cli_non_git_created_file_resolves_ok(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "app.py").write_text("print('hi')\n")
    tpath = tmp_path / "session.jsonl"
    _transcript(tpath, tmp_path, "I created app.py.", ["app.py"])

    rc = main(["check", "--transcript", str(tpath), "--no-art", "--no-color"])
    out = capsys.readouterr().out
    assert "app.py is there" in out          # resolved OK via transcript, no git
    assert "1 verified" in out               # counts-first tally
    assert "not a git repository" not in out  # no git noise
    assert rc == 0

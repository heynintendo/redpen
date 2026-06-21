"""Session-scoping: a creation claim needs evidence the agent touched the file."""

from __future__ import annotations

from redpen.changeset import ChangedSet, build_changed_set, normalize
from redpen.probes import file_present
from redpen.probes.base import ProbeContext, Verdict


def _changed_set(cwd, *touched):
    cs = ChangedSet()
    for rel in touched:
        cs.paths[normalize(cwd, rel)] = {"transcript"}
    return cs


def test_created_file_in_changeset_is_ok(tmp_path):
    (tmp_path / "app.py").write_text("print('hi')\n")
    ctx = ProbeContext(cwd=tmp_path, changed_set=_changed_set(tmp_path, "app.py"))

    res = file_present(ctx, path="app.py", created=True)

    assert res.verdict is Verdict.OK
    assert res.evidence["touched_this_session"] is True
    assert "transcript" in res.evidence["provenance"]


def test_preexisting_untouched_file_is_unverifiable_not_ok(tmp_path):
    # The file exists but the agent never touched it this session -> the
    # creation claim is UNVERIFIABLE (the silent false-OK this closes), not OK.
    (tmp_path / "preexisting.py").write_text("old code\n")
    ctx = ProbeContext(cwd=tmp_path, changed_set=_changed_set(tmp_path, "other.py"))

    res = file_present(ctx, path="preexisting.py", created=True)

    assert res.verdict is Verdict.UNVERIFIABLE
    assert res.evidence["touched_this_session"] is False


def test_missing_file_is_fail_regardless_of_scoping(tmp_path):
    ctx = ProbeContext(cwd=tmp_path, changed_set=_changed_set(tmp_path))
    res = file_present(ctx, path="gone.py", created=True)
    assert res.verdict is Verdict.FAIL


def test_no_changeset_degrades_to_existence(tmp_path):
    # Graceful degradation: without a changed-set we fall back to existence.
    (tmp_path / "x.py").write_text("content\n")
    res = file_present(ProbeContext(cwd=tmp_path), path="x.py", created=True)
    assert res.verdict is Verdict.OK


def test_non_creation_claim_is_not_scoped(tmp_path):
    # A plain presence check (created=False) is never session-scoped.
    (tmp_path / "x.py").write_text("content\n")
    ctx = ProbeContext(cwd=tmp_path, changed_set=_changed_set(tmp_path, "other.py"))
    res = file_present(ctx, path="x.py", created=False)
    assert res.verdict is Verdict.OK


def test_build_changed_set_merges_transcript_and_git(tmp_path):
    # A tiny git repo with one committed file and one untracked file.
    import subprocess

    env = {"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e.com",
           "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e.com"}

    def git(*a):
        import os
        subprocess.run(["git", *a], cwd=tmp_path, env={**os.environ, **env},
                       capture_output=True, text=True, check=True)

    git("init", "-b", "main")
    (tmp_path / "committed.py").write_text("x\n")
    git("add", "-A")
    git("-c", "commit.gpgsign=false", "commit", "-m", "init")
    (tmp_path / "untracked.py").write_text("y\n")  # git sees this as a change

    class _T:
        cwd = str(tmp_path)
        touched_files = ["from_transcript.py"]

    cs = build_changed_set(tmp_path, transcript=_T())
    assert cs.contains(tmp_path, "untracked.py")       # git signal
    assert cs.contains(tmp_path, "from_transcript.py")  # transcript signal
    assert "git" in cs.provenance(tmp_path, "untracked.py")


def test_git_only_change_cannot_attribute_authorship(tmp_path):
    # Another concurrent session changed the file: it shows up in the git/fs
    # delta but NOT as this session's transcript edit. Authorship is then
    # UNVERIFIABLE, never OK -- the cross-session false-OK guard.
    (tmp_path / "shared.py").write_text("v1\nedited by another session\n")
    cs = ChangedSet(is_git=True)
    cs.paths[normalize(tmp_path, "shared.py")] = {"git"}  # no "transcript" provenance

    res = file_present(ProbeContext(cwd=tmp_path, changed_set=cs), path="shared.py", created=True)

    assert res.verdict is Verdict.UNVERIFIABLE
    assert res.evidence.get("touched_this_session") is False

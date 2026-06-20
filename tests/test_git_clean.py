"""Edge cases for git_clean: untracked vs modified vs staged, ignored excluded."""

from __future__ import annotations

from redpen.probes import git_clean
from redpen.probes.base import ProbeContext, Verdict


def test_clean_tree_is_ok(git_repo, commit):
    commit(git_repo)
    res = git_clean(ProbeContext(cwd=git_repo))
    assert res.verdict is Verdict.OK
    assert res.evidence == {"staged": 0, "modified": 0, "untracked": 0}


def test_untracked_only_is_fail_and_categorized(git_repo, commit):
    commit(git_repo)
    (git_repo / "new.txt").write_text("hi")

    res = git_clean(ProbeContext(cwd=git_repo))

    assert res.verdict is Verdict.FAIL
    assert res.evidence["untracked"] == 1
    assert res.evidence["staged"] == 0 and res.evidence["modified"] == 0
    assert "untracked" in res.detail


def test_modified_tracked_file_is_fail(git_repo, commit):
    commit(git_repo, name="a.txt", content="one")
    (git_repo / "a.txt").write_text("two")  # modify a tracked file, unstaged

    res = git_clean(ProbeContext(cwd=git_repo))

    assert res.verdict is Verdict.FAIL
    assert res.evidence["modified"] == 1


def test_staged_change_is_fail(git_repo, commit, git):
    commit(git_repo, name="a.txt", content="one")
    (git_repo / "a.txt").write_text("two")
    git("add", "a.txt", cwd=git_repo)  # stage it

    res = git_clean(ProbeContext(cwd=git_repo))

    assert res.verdict is Verdict.FAIL
    assert res.evidence["staged"] == 1


def test_ignored_files_do_not_count_as_dirty(git_repo, git):
    (git_repo / ".gitignore").write_text("*.log\n")
    git("add", "-A", cwd=git_repo)
    git("-c", "commit.gpgsign=false", "commit", "-m", "ignore logs", cwd=git_repo)
    (git_repo / "debug.log").write_text("noise")  # matches .gitignore

    res = git_clean(ProbeContext(cwd=git_repo))

    assert res.verdict is Verdict.OK  # ignored file is not dirt


def test_committed_claim_in_non_git_folder_is_fail(tmp_path):
    # You can't have committed in a folder that isn't a repo -> contradiction.
    res = git_clean(ProbeContext(cwd=tmp_path))
    assert res.verdict is Verdict.FAIL
    assert "not a git repository" in res.detail

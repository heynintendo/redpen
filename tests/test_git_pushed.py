"""Behavioural tests for the git_pushed probe.

The claim under test is "pushed (to remote)". git_pushed compares HEAD against
the tracked upstream with ``git rev-list @{u}..HEAD`` -- a *local* operation,
no network. Verdict logic:

  * no upstream configured        -> UNVERIFIABLE (can't tell, not contradicted)
  * upstream exists, 0 ahead      -> OK
  * upstream exists, N ahead      -> FAIL (unpushed commits contradict "pushed")
  * not a git repo                -> UNVERIFIABLE
"""

from __future__ import annotations

import subprocess

from redpen.probes import git_pushed
from redpen.probes.base import ProbeContext, Verdict

_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def _git(*args, cwd):
    import os

    env = {**os.environ, **_ENV}
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True
    )


def _commit(cwd, name="f.txt", content="x"):
    (cwd / name).write_text(content)
    _git("add", "-A", cwd=cwd)
    _git("-c", "commit.gpgsign=false", "commit", "-m", f"add {name}", cwd=cwd)


def _repo_with_upstream(tmp_path):
    """A working clone of a bare remote, with the branch pushed and tracked."""
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git("init", "--bare", "-b", "main", cwd=remote)

    work = tmp_path / "work"
    work.mkdir()
    _git("init", "-b", "main", cwd=work)
    _git("remote", "add", "origin", str(remote), cwd=work)
    _commit(work)
    _git("push", "-u", "origin", "main", cwd=work)
    return work


def test_no_unpushed_commits_is_ok(tmp_path):
    work = _repo_with_upstream(tmp_path)

    res = git_pushed(ProbeContext(cwd=work))

    assert res.verdict is Verdict.OK
    assert res.evidence["ahead"] == 0


def test_unpushed_commits_is_fail(tmp_path):
    work = _repo_with_upstream(tmp_path)
    _commit(work, name="g.txt")  # committed but not pushed

    res = git_pushed(ProbeContext(cwd=work))

    assert res.verdict is Verdict.FAIL
    assert res.evidence["ahead"] == 1
    assert "1" in res.detail


def test_no_upstream_is_unverifiable(tmp_path):
    work = tmp_path / "solo"
    work.mkdir()
    _git("init", "-b", "main", cwd=work)
    _commit(work)

    res = git_pushed(ProbeContext(cwd=work))

    assert res.verdict is Verdict.UNVERIFIABLE


def test_not_a_git_repo_is_unverifiable(tmp_path):
    res = git_pushed(ProbeContext(cwd=tmp_path))
    assert res.verdict is Verdict.UNVERIFIABLE

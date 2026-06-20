"""Shared fixtures: throwaway git repos for the git-probe edge tests."""

from __future__ import annotations

import os
import subprocess

import pytest

_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def _git(*args, cwd):
    env = {**os.environ, **_ENV}
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True
    )


@pytest.fixture
def git():
    return _git


@pytest.fixture
def git_repo(tmp_path):
    work = tmp_path / "repo"
    work.mkdir()
    _git("init", "-b", "main", cwd=work)
    return work


@pytest.fixture
def commit():
    def _commit(cwd, name="f.txt", content="x"):
        (cwd / name).write_text(content)
        _git("add", "-A", cwd=cwd)
        _git("-c", "commit.gpgsign=false", "commit", "-m", f"add {name}", cwd=cwd)

    return _commit

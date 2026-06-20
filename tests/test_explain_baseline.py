"""Tests for `redpen explain` (+ last_run.json) and `redpen baseline`."""

from __future__ import annotations

import json
from pathlib import Path

from redpen.baseline import baseline_path, read_baseline, write_baseline
from redpen.cli import main
from redpen.lastrun import last_run_path, load_last_run

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "failing_session.jsonl"


def test_check_persists_numbered_last_run(monkeypatch, capsys):
    monkeypatch.chdir(REPO_ROOT)
    main(["check", "--transcript", str(FIXTURE), "--no-art", "--no-color"])
    capsys.readouterr()

    data = load_last_run(REPO_ROOT)
    assert data and data["findings"]
    assert [f["n"] for f in data["findings"]] == list(range(1, len(data["findings"]) + 1))
    # The contradiction FAIL is recorded with its quoted line.
    fails = [f for f in data["findings"] if f["verdict"] == "FAIL"]
    assert any("contradiction" in f["evidence"] for f in fails)


def test_explain_prints_commands_and_evidence(monkeypatch, capsys):
    monkeypatch.chdir(REPO_ROOT)
    main(["check", "I created README.md and pushed to origin", "--no-art", "--no-color"])
    capsys.readouterr()

    # git_pushed is verdict #2 here; explain it.
    data = load_last_run(REPO_ROOT)
    pushed = next(f for f in data["findings"] if f["probe"] == "git_pushed")
    rc = main(["explain", str(pushed["n"]), "--no-color"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "probe:" in out and "git_pushed" in out
    assert "commands run:" in out and "git rev-list" in out


def test_explain_last_and_missing(monkeypatch, capsys):
    monkeypatch.chdir(REPO_ROOT)
    main(["check", "tests pass", "--no-art", "--no-color"])
    capsys.readouterr()
    assert main(["explain", "last", "--no-color"]) == 0
    capsys.readouterr()
    assert main(["explain", "999", "--no-color"]) == 2  # no such verdict


def test_baseline_roundtrip(tmp_path, monkeypatch):
    import os
    import subprocess

    env = {"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e.com",
           "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e.com"}

    def git(*a):
        subprocess.run(["git", *a], cwd=tmp_path, env={**os.environ, **env},
                       capture_output=True, text=True, check=True)

    git("init", "-b", "main")
    (tmp_path / "a.txt").write_text("hello\n")
    git("add", "-A")
    git("-c", "commit.gpgsign=false", "commit", "-m", "init")
    (tmp_path / "a.txt").write_text("changed\n")  # now dirty

    path = write_baseline(tmp_path, session_id="s1")
    assert path == baseline_path(tmp_path)

    data = read_baseline(tmp_path)
    assert data["head"] and data["session_id"] == "s1"
    assert "a.txt" in data["hashes"]  # hashed the already-dirty file


def test_baseline_command_writes_file(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["baseline", "--quiet"]) == 0
    assert baseline_path(tmp_path).exists()

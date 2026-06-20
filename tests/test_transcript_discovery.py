"""Non-mocked tests for transcript discovery and the --transcript override.

These touch the real filesystem (and, for the end-to-end CLI test, the real
repo) but make NO network/LLM calls -- they exercise the deterministic path.
"""

from __future__ import annotations

from pathlib import Path

from redpen.cli import main
from redpen.transcript import (
    is_headless_transcript,
    latest_transcript_for,
    parse_transcript,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "sample_session.jsonl"


def test_fixture_parses_with_user_turn_and_entrypoint():
    t = parse_transcript(FIXTURE)
    assert t.entrypoint == "cli"  # a real session, not a headless claude -p call
    assert "create a config.py" in t.final_user_text
    assert "config.py" in t.final_assistant_text
    # the green pytest run is captured so tests_pass can read it
    assert any(e.tool == "Bash" and "pytest" in e.label and not e.failed for e in t.tool_events)


def test_transcript_override_end_to_end(monkeypatch, capsys):
    """--transcript bypasses discovery and verifies claims against the real repo."""
    monkeypatch.chdir(REPO_ROOT)
    rc = main(["check", "--transcript", str(FIXTURE), "--no-color", "--no-art"])

    out = capsys.readouterr().out
    assert "sample_session.jsonl" in out  # the source is surfaced
    assert "README.md present" in out  # exists in the repo -> OK
    assert "config.py does not exist" in out  # absent at repo root -> FAIL (real evidence)
    assert "pytest" in out and "ran clean" in out  # green run read from the transcript
    assert rc == 1  # exactly one real FAIL (config.py)


def test_missing_transcript_override_errors_cleanly(monkeypatch, capsys):
    monkeypatch.chdir(REPO_ROOT)
    rc = main(["check", "--transcript", "/no/such/transcript.jsonl", "--no-color"])
    assert rc == 2
    assert "transcript not found" in capsys.readouterr().err


def _write(path: Path, entrypoint: str):
    path.write_text(f'{{"type":"user","entrypoint":"{entrypoint}","sessionId":"x"}}\n', encoding="utf-8")


def test_is_headless_transcript(tmp_path):
    real = tmp_path / "real.jsonl"
    headless = tmp_path / "headless.jsonl"
    _write(real, "cli")
    _write(headless, "sdk-cli")
    assert is_headless_transcript(real) is False
    assert is_headless_transcript(headless) is True


def test_latest_transcript_skips_headless_even_if_newer(tmp_path):
    # tmp_path acts as $HOME; build ~/.claude/projects/<hash>/ for a fake project.
    projdir = tmp_path / ".claude" / "projects" / "-fake-proj"
    projdir.mkdir(parents=True)
    real = projdir / "real.jsonl"
    headless = projdir / "headless.jsonl"
    _write(real, "cli")
    _write(headless, "sdk-cli")
    # Make the headless one strictly newer so mtime-sort would prefer it.
    import os

    os.utime(real, (1_000, 1_000))
    os.utime(headless, (2_000, 2_000))

    picked = latest_transcript_for("/fake/proj", home=tmp_path)
    assert picked is not None
    assert picked.name == "real.jsonl"  # headless skipped despite being newer


def test_no_real_transcript_returns_none(tmp_path):
    projdir = tmp_path / ".claude" / "projects" / "-fake-proj"
    projdir.mkdir(parents=True)
    _write(projdir / "only-headless.jsonl", "sdk-cli")
    assert latest_transcript_for("/fake/proj", home=tmp_path) is None

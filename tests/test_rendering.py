"""Tests for verdict-marker rendering, humanized messages, and non-claim skipping."""

from __future__ import annotations

import json

from redpen import render
from redpen.claims import extract_claims
from redpen.cli import main
from redpen.config import TOOL_NAME
from redpen.engine import Finding
from redpen.probes.base import ProbeResult, Verdict
from redpen.render import Palette, _marker, render_report


class _TTY:
    def isatty(self):
        return True


class _NotTTY:
    def isatty(self):
        return False


def _finding(verdict, subject="something", detail="because reasons"):
    return Finding(claim_text=subject, source="adhoc", result=ProbeResult("p", verdict, detail), label=subject)


# --- markers: distinct color + shape + word ---------------------------------


def test_marker_colored_is_bright_bold_with_word():
    p = Palette(True)
    ok, fail, unsure = _marker(p, Verdict.OK), _marker(p, Verdict.FAIL), _marker(p, Verdict.UNVERIFIABLE)
    assert "● OK" in ok and "1;92" in ok            # bright-green dot, bold
    assert "● FAIL" in fail and "1;91" in fail        # bright-red dot, bold
    assert "▲ UNSURE" in unsure and "1;93" in unsure  # bright-amber triangle, bold
    # OK/FAIL share the dot shape but differ by color + word; UNSURE differs in shape
    assert ok != fail != unsure


def test_marker_no_color_falls_back_to_plain_labels():
    p = Palette(False)
    assert _marker(p, Verdict.OK).rstrip() == "[OK]"
    assert _marker(p, Verdict.FAIL).rstrip() == "[FAIL]"
    assert _marker(p, Verdict.UNVERIFIABLE).rstrip() == "[ ? ]"
    assert all("\x1b" not in _marker(p, v) for v in Verdict)  # no ANSI when off


def test_markers_pad_to_one_width_so_columns_stack():
    for p in (Palette(False),):
        widths = {len(_marker(p, v)) for v in Verdict}
        assert len(widths) == 1  # OK/FAIL/UNSURE all the same visible width


def test_report_no_color_uses_bracket_labels_and_no_ansi():
    findings = [_finding(Verdict.OK), _finding(Verdict.FAIL), _finding(Verdict.UNVERIFIABLE)]
    out = render_report(findings, show_art=False, color=False)
    assert "[OK]" in out and "[FAIL]" in out and "[ ? ]" in out
    assert "\x1b" not in out


def test_report_color_emits_bright_bold_markers():
    out = render_report([_finding(Verdict.OK)], show_art=False, color=True)
    assert "\x1b[1;92m" in out and "● OK" in out


# --- copy: counts-first tally, plain words, one optional subhead -------------


def test_headline_leads_with_the_plain_tally():
    # 1 OK, 2 UNV, 1 FAIL -> the first non-empty line is the tally, plain words.
    findings = [_finding(Verdict.OK), _finding(Verdict.UNVERIFIABLE),
                _finding(Verdict.UNVERIFIABLE), _finding(Verdict.FAIL)]
    out = render_report(findings, show_art=False, color=False)
    first = out.strip().splitlines()[0]
    assert first == "1 verified · 2 can't confirm · 1 failed"
    assert "UNVERIFIABLE" not in out and "Marked." not in out  # no jargon, no old voice


def test_all_pass_tally_and_subhead():
    out = render_report([_finding(Verdict.OK)], show_art=False, color=False)
    assert "1 verified · 0 can't confirm · 0 failed" in out
    assert "All clear." in out


def test_some_fail_subhead_points_at_the_failure():
    out = render_report([_finding(Verdict.FAIL), _finding(Verdict.OK)], show_art=False, color=False)
    assert "1 verified · 0 can't confirm · 1 failed" in out
    assert "Look at the failed line first." in out


def test_mixed_ok_and_unsure_subhead():
    out = render_report([_finding(Verdict.OK), _finding(Verdict.UNVERIFIABLE)], show_art=False, color=False)
    assert "1 verified · 1 can't confirm · 0 failed" in out
    assert "The rest checks out." in out


def test_all_unsure_tally(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)  # empty non-git folder, no transcript
    main(["check", "the tests pass", "--no-art", "--no-color"])
    out = capsys.readouterr().out
    assert "0 verified · 1 can't confirm · 0 failed" in out
    assert "Nothing here could be checked this session." in out


# --- terminal capability tiers ----------------------------------------------


def test_color_level_truecolor(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setenv("COLORTERM", "truecolor")
    monkeypatch.setenv("TERM", "xterm-256color")
    assert render._color_level(_TTY()) == 3


def test_color_level_256_when_no_colorterm(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("COLORTERM", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    assert render._color_level(_TTY()) == 2


def test_color_level_16_basic_term(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("COLORTERM", raising=False)
    monkeypatch.setenv("TERM", "xterm")
    assert render._color_level(_TTY()) == 1


def test_color_level_none_when_no_color_or_non_tty(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert render._color_level(_TTY()) == 0
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    assert render._color_level(_NotTTY()) == 0


# --- mascot degrades by capability ------------------------------------------


def test_mascot_truecolor_then_256_then_ascii():
    t3, t2 = render.load_mascot_art(3), render.load_mascot_art(2)
    assert t3 and "38;2;" in t3                                  # full truecolor art
    assert t2 and "38;5;" in t2 and "38;2;" not in t2            # 256-color, no 24-bit escapes
    assert render.load_mascot_art(1) is None                    # below 256 -> ASCII fallback
    block = render._header_block(Palette(True), True, 1)
    assert "\x1b[38;2" not in block and TOOL_NAME in block       # clean ASCII mascot, named


# --- reasons read fully, never cut mid-word ---------------------------------


def test_long_reason_wraps_and_is_not_cut_midword(monkeypatch):
    monkeypatch.setenv("COLUMNS", "100")
    long = ("nothing ran this session and there is no runner configured here, "
            "so I cannot confirm the linter is clean")
    out = render_report([_finding(Verdict.UNVERIFIABLE, "the linter is clean", long)],
                        show_art=False, color=False)
    for word in long.replace(",", "").split():
        assert word in out, f"reason word dropped: {word}"


# --- nothing to grade -------------------------------------------------------


def test_nothing_to_grade_is_human(tmp_path, monkeypatch, capsys):
    cwd = str(tmp_path)
    lines = [{"type": "assistant", "sessionId": "s", "cwd": cwd, "entrypoint": "cli",
              "message": {"role": "assistant", "content": [{"type": "text", "text": "Fresh session. Nothing to report."}]}}]
    t = tmp_path / "s.jsonl"
    t.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    monkeypatch.chdir(tmp_path)

    main(["check", "--transcript", str(t), "--no-art", "--no-color"])
    out = capsys.readouterr().out
    assert "Nothing to grade" in out and "didn't actually claim" in out


# --- descriptive listings are not claims ------------------------------------


def test_directory_listing_produces_no_verdicts():
    text = (
        "Here's the structure:\n"
        "redpen/cli.py — the CLI entry point\n"
        "redpen/render.py — rendering\n"
        "tests/ — the test suite\n"
        "config.py — created for settings\n"
        "src/app.py"
    )
    assert extract_claims(text, source="transcript") == []


def test_real_creation_claim_survives_amid_a_listing():
    text = "I created redpen/newthing.py.\nredpen/cli.py — the CLI\ntests/ — suite"
    claims = extract_claims(text, source="transcript")
    names = {s.name for c in claims for s in c.probe_specs}
    assert names == {"file_present"}  # only the real "I created X" line counts

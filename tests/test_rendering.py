"""Tests for verdict-marker rendering, humanized messages, and non-claim skipping."""

from __future__ import annotations

import json

from redpen.claims import extract_claims
from redpen.cli import main
from redpen.engine import Finding
from redpen.probes.base import ProbeResult, Verdict
from redpen.render import Palette, _marker, render_report


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
    assert _marker(p, Verdict.UNVERIFIABLE).rstrip() == "[??]"
    assert all("\x1b" not in _marker(p, v) for v in Verdict)  # no ANSI when off


def test_markers_pad_to_one_width_so_columns_stack():
    for p in (Palette(False),):
        widths = {len(_marker(p, v)) for v in Verdict}
        assert len(widths) == 1  # OK/FAIL/UNSURE all the same visible width


def test_report_no_color_uses_bracket_labels_and_no_ansi():
    findings = [_finding(Verdict.OK), _finding(Verdict.FAIL), _finding(Verdict.UNVERIFIABLE)]
    out = render_report(findings, show_art=False, color=False)
    assert "[OK]" in out and "[FAIL]" in out and "[??]" in out
    assert "\x1b" not in out


def test_report_color_emits_bright_bold_markers():
    out = render_report([_finding(Verdict.OK)], show_art=False, color=True)
    assert "\x1b[1;92m" in out and "● OK" in out


# --- humanized headlines for every state ------------------------------------


def test_all_pass_headline_keeps_the_voice():
    out = render_report([_finding(Verdict.OK)], show_art=False, color=False)
    assert "Everything checks out. Don't get used to it." in out


def test_some_fail_headline():
    out = render_report([_finding(Verdict.FAIL), _finding(Verdict.OK)], show_art=False, color=False)
    assert "doesn't hold up" in out


def test_mixed_ok_and_unsure_headline():
    out = render_report([_finding(Verdict.OK), _finding(Verdict.UNVERIFIABLE)], show_art=False, color=False)
    assert "The rest holds up" in out and "can't confirm" in out.lower()


def test_all_unsure_headline_is_human(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)  # empty non-git folder, no transcript
    main(["check", "the tests pass", "--no-art", "--no-color"])
    out = capsys.readouterr().out
    assert "Can't confirm any of these" in out
    assert "not wrong, just unverified" in out


def test_footer_uses_unsure_label():
    out = render_report([_finding(Verdict.UNVERIFIABLE)], show_art=False, color=False, elapsed=0.1)
    assert "unsure" in out and "UNVERIFIABLE" not in out


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

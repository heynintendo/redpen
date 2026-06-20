"""Tests for the Phase 2 LLM layer, with the `claude -p` subprocess mocked.

No real network or subscription is touched: every test patches the single
subprocess seam (``judge._run_claude`` or ``subprocess.run``). These cover the
contract that matters -- verdict parsing, the precision-preserving fallbacks,
request decomposition, and the two hard safety constraints (API key unset,
hooks disabled).
"""

from __future__ import annotations

import json

from redpen import config, judge
from redpen.engine import verify
from redpen.probes.base import ProbeContext, ProbeResult, ProbeSpec, Verdict


def _envelope(reply_text: str, is_error: bool = False) -> str:
    """Mimic the `claude --output-format json` envelope."""
    return json.dumps({"type": "result", "is_error": is_error, "result": reply_text})


def _unverifiable(detail="no upstream configured", **ev):
    return ProbeResult("git_pushed", Verdict.UNVERIFIABLE, detail, ev or {"ahead": 0})


# --- judge_claim: JSON parsing ---------------------------------------------


def test_judge_parses_ok_verdict(monkeypatch):
    monkeypatch.setattr(
        judge, "_run_claude",
        lambda prompt, model, timeout: (0, _envelope('{"verdict":"OK","reason":"level with upstream"}'), ""),
    )
    res = judge.judge_claim("pushed to origin", _unverifiable())
    assert res.verdict is Verdict.OK
    assert "level with upstream" in res.detail
    assert res.evidence["judge"]["verdict"] == "OK"


def test_judge_extracts_verdict_from_prose_wrapped_json(monkeypatch):
    reply = 'Sure, here is the verdict:\n{"verdict":"FAIL","reason":"2 commits unpushed"}'
    monkeypatch.setattr(judge, "_run_claude", lambda p, m, t: (0, _envelope(reply), ""))
    res = judge.judge_claim("pushed", _unverifiable())
    assert res.verdict is Verdict.FAIL
    assert "unpushed" in res.detail


def test_judge_passes_claim_and_evidence_in_prompt(monkeypatch):
    seen = {}

    def fake(prompt, model, timeout):
        seen["prompt"] = prompt
        return (0, _envelope('{"verdict":"OK","reason":"ok"}'), "")

    monkeypatch.setattr(judge, "_run_claude", fake)
    judge.judge_claim("pushed to origin", _unverifiable(ahead=0, upstream="origin/main"))
    # The model must receive the claim and the gathered evidence (never the codebase).
    assert "pushed to origin" in seen["prompt"]
    assert "origin/main" in seen["prompt"]


# --- judge_claim: UNVERIFIABLE-on-failure fallback --------------------------


def test_judge_falls_back_to_unverifiable_on_subprocess_failure(monkeypatch):
    monkeypatch.setattr(judge, "_run_claude", lambda p, m, t: (127, "", "claude CLI not found on PATH"))
    res = judge.judge_claim("pushed", _unverifiable())
    assert res.verdict is Verdict.UNVERIFIABLE
    assert "claude CLI not found" in res.detail


def test_judge_falls_back_on_unparseable_output(monkeypatch):
    monkeypatch.setattr(judge, "_run_claude", lambda p, m, t: (0, "not json at all", ""))
    res = judge.judge_claim("pushed", _unverifiable())
    assert res.verdict is Verdict.UNVERIFIABLE


def test_judge_falls_back_on_unknown_verdict(monkeypatch):
    monkeypatch.setattr(judge, "_run_claude", lambda p, m, t: (0, _envelope('{"verdict":"MAYBE"}'), ""))
    res = judge.judge_claim("pushed", _unverifiable())
    assert res.verdict is Verdict.UNVERIFIABLE


def test_judge_never_guesses_fail_on_error_envelope(monkeypatch):
    monkeypatch.setattr(judge, "_run_claude", lambda p, m, t: (0, _envelope("whatever", is_error=True), ""))
    res = judge.judge_claim("pushed", _unverifiable())
    assert res.verdict is Verdict.UNVERIFIABLE


# --- request decomposition --------------------------------------------------


def test_decompose_request_parses_array(monkeypatch):
    reply = '["create README.md", "wire the LLM judge", "push to origin"]'
    monkeypatch.setattr(judge, "_run_claude", lambda p, m, t: (0, _envelope(reply), ""))
    items = judge.decompose_request("please do three things ...")
    assert items == ["create README.md", "wire the LLM judge", "push to origin"]


def test_decompose_request_empty_on_failure(monkeypatch):
    monkeypatch.setattr(judge, "_run_claude", lambda p, m, t: (127, "", "no claude"))
    assert judge.decompose_request("do stuff") == []


def test_decompose_request_empty_on_blank_input(monkeypatch):
    called = {"n": 0}

    def fake(*a, **k):
        called["n"] += 1
        return (0, _envelope("[]"), "")

    monkeypatch.setattr(judge, "_run_claude", fake)
    assert judge.decompose_request("   ") == []
    assert called["n"] == 0  # no LLM call for an empty request


# --- request audit ----------------------------------------------------------


def test_audit_request_parses_and_normalizes_status(monkeypatch):
    reply = json.dumps(
        [
            {"item": "create README.md", "status": "DONE", "note": "present"},
            {"item": "push to origin", "status": "skipped", "note": "no upstream"},
            {"item": "tests pass", "status": "weird", "note": "?"},
        ]
    )
    monkeypatch.setattr(judge, "_run_claude", lambda p, m, t: (0, _envelope(reply), ""))
    out = judge.audit_request(
        "create README.md, push to origin, and make the tests pass",
        claimed=["created README.md", "pushed", "tests pass"],
        verdicts=[("wrote README.md", "OK", "present")],
    )
    # An unrecognized status normalizes to UNVERIFIABLE (never a guessed SKIPPED).
    assert [o["status"] for o in out] == ["DONE", "SKIPPED", "UNVERIFIABLE"]


def test_audit_request_passes_request_and_evidence_in_one_call(monkeypatch):
    seen = {}

    def fake(prompt, model, timeout):
        seen["prompt"] = prompt
        return (0, _envelope("[]"), "")

    monkeypatch.setattr(judge, "_run_claude", fake)
    judge.audit_request("add a --version flag", claimed=["did X"], verdicts=[("x", "OK", "y")])
    # The single call must contain the raw request (to decompose) AND the evidence.
    assert "add a --version flag" in seen["prompt"]
    assert "REDPEN VERDICTS" in seen["prompt"]


def test_audit_request_empty_without_request(monkeypatch):
    monkeypatch.setattr(judge, "_run_claude", lambda p, m, t: (0, _envelope("[]"), ""))
    assert judge.audit_request("", claimed=[], verdicts=[]) == []


# --- engine precision gate: judge only touches UNVERIFIABLE -----------------


def test_engine_only_judges_unverifiable_results(monkeypatch):
    from redpen.claims import Claim

    seen: list[str] = []

    def fake_judge(claim_text, result):
        seen.append(result.probe)
        return ProbeResult(result.probe, Verdict.OK, "judged", {})

    # file_present on a real present file -> OK (deterministic), must NOT be judged.
    # A missing file -> FAIL, must NOT be judged either. Only UNVERIFIABLE is.
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "here.txt").write_text("x")
        claim = Claim(
            text="created files",
            probe_specs=[
                ProbeSpec("file_present", {"path": "here.txt"}),   # -> OK
                ProbeSpec("file_present", {"path": "gone.txt"}),   # -> FAIL
                ProbeSpec("tests_pass"),                           # -> UNVERIFIABLE (no runner)
            ],
        )
        findings = verify([claim], ProbeContext(cwd=Path(d)), judge=fake_judge)

    verdicts = {f.result.probe: f.result.verdict for f in findings}
    # Only the UNVERIFIABLE one (tests_pass) was sent to the judge.
    assert seen == ["tests_pass"]
    assert verdicts["tests_pass"] is Verdict.OK  # upgraded by the judge


# --- safety constraints: real _run_claude, subprocess.run mocked ------------


def test_run_claude_unsets_api_key_and_disables_hooks(monkeypatch):
    captured = {}

    def fake_run(cmd, capture_output, text, timeout, env, cwd):
        captured["cmd"] = cmd
        captured["env"] = env
        captured["cwd"] = cwd
        # Read the temp settings file while it still exists.
        idx = cmd.index("--settings")
        captured["settings"] = json.loads(open(cmd[idx + 1]).read())

        class R:
            returncode = 0
            stdout = _envelope('{"verdict":"OK","reason":"ok"}')
            stderr = ""

        return R()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-be-removed")
    monkeypatch.setattr(judge.subprocess, "run", fake_run)

    rc, out, err = judge._run_claude("prompt", config.LLM_MODEL, 5)

    assert rc == 0
    assert "ANTHROPIC_API_KEY" not in captured["env"]            # forced onto the subscription
    assert captured["settings"] == {"disableAllHooks": True}     # no recursive RedPen
    assert captured["cmd"][:2] == ["claude", "-p"]
    assert "--output-format" in captured["cmd"] and "json" in captured["cmd"]
    # Runs from a neutral dir, NOT a project dir -> its transcript can't pollute
    # the project's transcript dir (and so can't be auto-discovered later).
    import tempfile

    assert captured["cwd"] == tempfile.gettempdir()


# --- determinism cache ------------------------------------------------------


def test_judge_caches_verdict_by_evidence(monkeypatch, tmp_path):
    calls = {"n": 0}

    def fake(prompt, model, timeout):
        calls["n"] += 1
        return (0, _envelope('{"verdict":"OK","reason":"level"}'), "")

    monkeypatch.setattr(judge, "_run_claude", fake)
    base = _unverifiable(ahead=0, upstream="origin/main")

    r1 = judge.judge_claim("pushed", base, cache_dir=tmp_path)
    r2 = judge.judge_claim("pushed", base, cache_dir=tmp_path)

    assert r1.verdict is Verdict.OK and r2.verdict is Verdict.OK
    assert calls["n"] == 1  # second call served from the cache, no re-spend
    assert r2.evidence["judge"].get("cached") is True
    assert (tmp_path / "judge_cache.json").exists()


def test_judge_does_not_cache_fallbacks(monkeypatch, tmp_path):
    monkeypatch.setattr(judge, "_run_claude", lambda p, m, t: (127, "", "no claude"))
    r = judge.judge_claim("pushed", _unverifiable(), cache_dir=tmp_path)
    assert r.verdict is Verdict.UNVERIFIABLE
    # Nothing cached, so a later (working) call would still try.
    import json as _json
    cache = _json.loads((tmp_path / "judge_cache.json").read_text()) if (tmp_path / "judge_cache.json").exists() else {}
    assert cache == {}

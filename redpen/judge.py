"""Phase 2: the optional LLM judgment layer.

The judge looks ONLY at the structured evidence a probe already gathered (git
summaries, exit codes, file states, test output). It NEVER reads or explores
the codebase -- that re-exploration is the slow, unreliable failure mode RedPen
exists to prevent. Its sole job is to resolve some UNVERIFIABLE results into
OK/FAIL when the evidence is richer than a deterministic rule can exploit.

How it runs
-----------
A single headless turn of the user's own Claude Code:

    claude -p "<prompt>" --output-format json --model <config.LLM_MODEL> \
           --settings <tmp: {"disableAllHooks": true}>

with the child's ANTHROPIC_API_KEY unset (force the existing subscription, no
per-token API billing) and hooks disabled (so the spawned call can't recursively
re-trigger RedPen). No API key required; Claude Code must be installed + logged in.

Precision is preserved: FAIL only when the evidence contradicts the claim;
missing or ambiguous evidence is always UNVERIFIABLE. Any failure, timeout, or
unparseable reply falls back to UNVERIFIABLE -- never a crash, never a guessed FAIL.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from . import config
from .probes.base import ProbeResult, Verdict

# A judge refines one (claim, probe-result) into a better verdict, or returns
# the input unchanged if it can't improve on it.
Judge = Callable[[str, ProbeResult], ProbeResult]

_VALID = {"OK": Verdict.OK, "FAIL": Verdict.FAIL, "UNVERIFIABLE": Verdict.UNVERIFIABLE}


def _run_claude(prompt: str, model: str, timeout: float) -> tuple[int, str, str]:
    """Invoke one headless `claude -p` turn. Isolated so tests can patch it.

    Returns (returncode, stdout, stderr). A missing `claude` binary or a timeout
    is returned as a non-zero code rather than raised.
    """
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)  # force the subscription, not the API

    settings_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", prefix="redpen-settings-", delete=False
        ) as fh:
            json.dump({"disableAllHooks": True}, fh)  # no recursive RedPen triggers
            settings_path = fh.name

        proc = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "json", "--model", model,
             "--settings", settings_path],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            # Run from a neutral dir: Claude Code writes a transcript keyed on
            # cwd, so running here (not the project) keeps RedPen's own headless
            # calls out of the project's transcript dir (belt-and-suspenders with
            # the sdk-cli discovery filter). The judge needs no project files --
            # all evidence is in the prompt.
            cwd=tempfile.gettempdir(),
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"claude timed out after {timeout}s"
    except FileNotFoundError:
        return 127, "", "claude CLI not found on PATH"
    finally:
        if settings_path:
            try:
                os.unlink(settings_path)
            except OSError:
                pass


def _extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of model text (tolerates fences/prose)."""
    if not text:
        return None
    try:
        obj = json.loads(text.strip())
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, ValueError):
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            obj = json.loads(match.group(0))
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def _verdict_prompt(claim: str, result: ProbeResult) -> str:
    evidence = json.dumps(result.evidence, default=str)[:1500]
    return (
        "You are RedPen's verifier. Decide whether a claim is substantiated by "
        "the gathered evidence ALONE. You cannot see the codebase; assume nothing "
        "that is not in the evidence.\n\n"
        f"CLAIM: {claim}\n"
        f"PROBE: {result.probe}\n"
        f"PROBE_READING: {result.detail}\n"
        f"EVIDENCE: {evidence}\n\n"
        "Rules:\n"
        '- "OK" only if the evidence clearly substantiates the claim.\n'
        '- "FAIL" only if the evidence clearly contradicts the claim.\n'
        '- "UNVERIFIABLE" if the evidence is missing, partial, or ambiguous.\n'
        "Reply with ONE line of JSON and nothing else: "
        '{"verdict":"OK|FAIL|UNVERIFIABLE","reason":"<=12 words"}'
    )


def _fallback(result: ProbeResult, note: str) -> ProbeResult:
    """Keep the deterministic UNVERIFIABLE, annotated with why the judge bowed out."""
    evidence = {**result.evidence, "judge": {"status": "fallback", "note": note}}
    return ProbeResult(result.probe, Verdict.UNVERIFIABLE, f"{result.detail} (judge: {note})", evidence)


# --- determinism cache ------------------------------------------------------
# Identical evidence always yields the identical verdict, persisted in
# .redpen/judge_cache.json, so a re-run never re-spends quota or flip-flops.
_CACHE_FILE = "judge_cache.json"
# Evidence keys that vary between runs but don't change the judgement.
_VOLATILE = {"mtime", "commands", "judge"}


def _evidence_key(claim: str, result: ProbeResult) -> str:
    import hashlib

    stable = {k: v for k, v in (result.evidence or {}).items() if k not in _VOLATILE}
    blob = json.dumps(
        {"claim": claim, "probe": result.probe, "detail": result.detail, "evidence": stable},
        sort_keys=True, default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _load_cache(cache_dir) -> dict:
    if not cache_dir:
        return {}
    path = Path(cache_dir) / _CACHE_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(cache_dir, cache: dict) -> None:
    if not cache_dir:
        return
    path = Path(cache_dir) / _CACHE_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def judge_claim(
    claim: str,
    result: ProbeResult,
    model: str | None = None,
    timeout: float | None = None,
    cache_dir=None,
) -> ProbeResult:
    """Ask the LLM to resolve an UNVERIFIABLE result. Never raises.

    Returns a refined ProbeResult, or an UNVERIFIABLE fallback on any failure /
    timeout / parse error. Only ever called on UNVERIFIABLE results (the engine
    gates it), so deterministic OK/FAIL verdicts are never overridden. Verdicts
    are cached by evidence hash, so identical evidence never re-spends quota.
    """
    model = model or config.LLM_MODEL
    timeout = timeout if timeout is not None else config.JUDGE_TIMEOUT_SECONDS

    key = _evidence_key(claim, result)
    cache = _load_cache(cache_dir)
    cached = cache.get(key)
    if cached and cached.get("verdict") in _VALID:
        evidence = {**result.evidence, "judge": {**cached, "cached": True}}
        return ProbeResult(result.probe, _VALID[cached["verdict"]], f"judge: {cached.get('reason', '')} (cached)", evidence)

    rc, stdout, stderr = _run_claude(_verdict_prompt(claim, result), model, timeout)
    if rc != 0:
        return _fallback(result, (stderr or "claude call failed").strip().splitlines()[0][:60])

    # `claude --output-format json` wraps the reply: {"result": "<text>", ...}.
    envelope = _extract_json(stdout)
    if envelope is None:
        return _fallback(result, "unparseable claude output")
    if envelope.get("is_error"):
        return _fallback(result, "claude reported an error")
    reply_text = envelope.get("result", stdout) if isinstance(envelope, dict) else stdout

    parsed = _extract_json(reply_text if isinstance(reply_text, str) else json.dumps(reply_text))
    if not parsed or "verdict" not in parsed:
        return _fallback(result, "no verdict in reply")

    vkey = str(parsed.get("verdict", "")).strip().upper()
    verdict = _VALID.get(vkey)
    if verdict is None:
        return _fallback(result, f"unknown verdict '{vkey[:20]}'")

    reason = str(parsed.get("reason", "")).strip()[:80] or "no reason given"
    # Cache only real verdicts -- never the transient fallbacks above, so a
    # temporary claude outage doesn't get frozen into the cache.
    cache[key] = {"model": model, "verdict": vkey, "reason": reason}
    _save_cache(cache_dir, cache)
    evidence = {**result.evidence, "judge": cache[key]}
    return ProbeResult(result.probe, verdict, f"judge: {reason}", evidence)


def make_judge(model: str | None = None, timeout: float | None = None, cache_dir=None) -> Judge:
    """Build the judge callable the engine seam expects: judge(claim, result)."""
    return lambda claim, result: judge_claim(
        claim, result, model=model, timeout=timeout, cache_dir=cache_dir
    )


# --- full-request audit (the /checkall extras) ------------------------------


def _extract_json_array(text: str) -> list | None:
    """Pull the first JSON array out of model text (tolerates fences/prose)."""
    if not text:
        return None
    try:
        obj = json.loads(text.strip())
        if isinstance(obj, list):
            return obj
    except (json.JSONDecodeError, ValueError):
        pass
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try:
            obj = json.loads(match.group(0))
            if isinstance(obj, list):
                return obj
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def _reply_text(stdout: str) -> str | None:
    """Unwrap the `claude --output-format json` envelope to the model's reply."""
    envelope = _extract_json(stdout)
    if envelope is None or envelope.get("is_error"):
        return None
    reply = envelope.get("result", stdout)
    return reply if isinstance(reply, str) else json.dumps(reply)


def decompose_request(
    user_text: str, model: str | None = None, timeout: float | None = None
) -> list[str]:
    """Break the user's last request into concrete, checkable deliverables.

    Returns a list of short strings (empty on any failure). Uses one headless
    `claude -p` turn -- acceptable on the deep path.
    """
    model = model or config.LLM_MODEL
    timeout = timeout if timeout is not None else config.JUDGE_TIMEOUT_SECONDS
    user_text = (user_text or "").strip()
    if not user_text:
        return []

    prompt = (
        "Break the user's request into the concrete, separately-checkable things "
        "they asked for. Output ONLY a JSON array of short strings (max 8 items), "
        "each one deliverable, no prose.\n\n"
        f"REQUEST:\n{user_text[:3000]}"
    )
    rc, stdout, _ = _run_claude(prompt, model, timeout)
    if rc != 0:
        return []
    reply = _reply_text(stdout)
    if reply is None:
        return []
    arr = _extract_json_array(reply)
    if not arr:
        return []
    items = [str(x).strip() for x in arr if str(x).strip()]
    return items[:8]


def audit_request(
    request_text: str,
    claimed: list[str],
    verdicts: list[tuple[str, str, str]],
    model: str | None = None,
    timeout: float | None = None,
) -> list[dict]:
    """Audit a request end-to-end in ONE call: decompose it, then reconcile.

    Takes the raw user request, breaks it into the things asked for, and
    classifies each against what the assistant claimed and RedPen's evidence
    verdicts -- all in a single headless turn (half the latency/cost of doing
    decompose and reconcile separately). ``verdicts`` is (subject, verdict,
    detail). Returns [{item, status, note}] with status DONE | UNSUBSTANTIATED |
    SKIPPED. Empty on any failure. Sees ONLY the provided text, never the codebase.
    """
    model = model or config.LLM_MODEL
    timeout = timeout if timeout is not None else config.JUDGE_TIMEOUT_SECONDS
    request_text = (request_text or "").strip()
    if not request_text:
        return []

    claimed_block = "\n".join(f"- {c}" for c in claimed[:20]) or "- (the assistant claimed nothing specific)"
    verdict_block = "\n".join(f"- {s} -> {v}: {d}" for s, v, d in verdicts[:30]) or "- (no probe verdicts)"

    prompt = (
        "You audit whether a coding request was fully satisfied. You see ONLY: "
        "the user's request, what the assistant claimed, and RedPen's evidence-based "
        "verdicts. You cannot see the codebase; assume nothing beyond this.\n\n"
        f"USER REQUEST:\n{request_text[:3000]}\n\n"
        f"ASSISTANT CLAIMED:\n{claimed_block}\n\n"
        f"REDPEN VERDICTS (subject -> verdict: detail):\n{verdict_block}\n\n"
        "First break the request into the concrete, separately-checkable things "
        "EXPLICITLY asked for (max 8). Do NOT invent items the user did not ask "
        "for. Then classify each:\n"
        '- "DONE": addressed by a claim AND RedPen evidence supports it.\n'
        '- "UNSUBSTANTIATED": claimed but evidence is FAIL/UNVERIFIABLE or absent.\n'
        '- "SKIPPED": ONLY when it was clearly requested, no claim addressed it, '
        "AND no evidence suggests it happened.\n"
        '- "UNVERIFIABLE": use this whenever you are unsure -- e.g. it might have '
        "been done but isn't claimed/evidenced. Prefer this over a guessed SKIPPED.\n"
        "Output ONLY a JSON array: "
        '[{"item":"<asked-for item>","status":"DONE|UNSUBSTANTIATED|SKIPPED|UNVERIFIABLE","note":"<=12 words"}]'
    )
    rc, stdout, _ = _run_claude(prompt, model, timeout)
    if rc != 0:
        return []
    reply = _reply_text(stdout)
    if reply is None:
        return []
    arr = _extract_json_array(reply)
    if not arr:
        return []

    valid_status = {"DONE", "UNSUBSTANTIATED", "SKIPPED", "UNVERIFIABLE"}
    out: list[dict] = []
    for entry in arr:
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status", "")).strip().upper()
        # Anything we don't recognize collapses to UNVERIFIABLE, never a guessed SKIPPED.
        out.append(
            {
                "item": str(entry.get("item", "")).strip()[:80],
                "status": status if status in valid_status else "UNVERIFIABLE",
                "note": str(entry.get("note", "")).strip()[:80],
            }
        )
    return out

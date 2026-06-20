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


def judge_claim(
    claim: str,
    result: ProbeResult,
    model: str | None = None,
    timeout: float | None = None,
) -> ProbeResult:
    """Ask the LLM to resolve an UNVERIFIABLE result. Never raises.

    Returns a refined ProbeResult, or an UNVERIFIABLE fallback on any failure /
    timeout / parse error. Only ever called on UNVERIFIABLE results (the engine
    gates it), so deterministic OK/FAIL verdicts are never overridden.
    """
    model = model or config.LLM_MODEL
    timeout = timeout if timeout is not None else config.JUDGE_TIMEOUT_SECONDS

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
    evidence = {**result.evidence, "judge": {"model": model, "verdict": vkey, "reason": reason}}
    return ProbeResult(result.probe, verdict, f"judge: {reason}", evidence)


def make_judge(model: str | None = None, timeout: float | None = None) -> Judge:
    """Build the judge callable the engine seam expects: judge(claim, result)."""
    return lambda claim, result: judge_claim(claim, result, model=model, timeout=timeout)


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
    asked: list[str],
    claimed: list[str],
    verdicts: list[tuple[str, str, str]],
    model: str | None = None,
    timeout: float | None = None,
) -> list[dict]:
    """Reconcile asked-for items against claims and RedPen's evidence verdicts.

    ``verdicts`` is a list of (subject, verdict, detail). Returns a list of
    {item, status, note} where status is DONE | UNSUBSTANTIATED | SKIPPED.
    Empty on any failure. Sees ONLY the provided text -- never the codebase.
    """
    model = model or config.LLM_MODEL
    timeout = timeout if timeout is not None else config.JUDGE_TIMEOUT_SECONDS
    if not asked:
        return []

    asked_block = "\n".join(f"{i + 1}. {a}" for i, a in enumerate(asked))
    claimed_block = "\n".join(f"- {c}" for c in claimed[:20]) or "- (the assistant claimed nothing specific)"
    verdict_block = "\n".join(f"- {s} -> {v}: {d}" for s, v, d in verdicts[:30]) or "- (no probe verdicts)"

    prompt = (
        "You audit whether a coding request was fully satisfied. You see ONLY: "
        "what was asked, what the assistant claimed, and RedPen's evidence-based "
        "verdicts. You cannot see the codebase; assume nothing beyond this.\n\n"
        f"ASKED:\n{asked_block}\n\n"
        f"ASSISTANT CLAIMED:\n{claimed_block}\n\n"
        f"REDPEN VERDICTS (subject -> verdict: detail):\n{verdict_block}\n\n"
        "For each ASKED item, classify:\n"
        '- "DONE": addressed by a claim AND RedPen evidence supports it.\n'
        '- "UNSUBSTANTIATED": claimed but evidence is FAIL or UNVERIFIABLE.\n'
        '- "SKIPPED": not addressed by any claim.\n'
        "Output ONLY a JSON array: "
        '[{"item":"<asked item>","status":"DONE|UNSUBSTANTIATED|SKIPPED","note":"<=12 words"}]'
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

    valid_status = {"DONE", "UNSUBSTANTIATED", "SKIPPED"}
    out: list[dict] = []
    for entry in arr:
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status", "")).strip().upper()
        out.append(
            {
                "item": str(entry.get("item", "")).strip()[:80],
                "status": status if status in valid_status else "UNKNOWN",
                "note": str(entry.get("note", "")).strip()[:80],
            }
        )
    return out

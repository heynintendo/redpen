"""RedPen command-line interface.

    redpen check                 auto: extract claims from the latest transcript
    redpen check "<question>"    verify one ad-hoc claim
    redpen check --run           permit re-running tests/build/lint
    redpen check --no-art        suppress the mascot
    redpen check --deep          LLM judge + full-request audit (uses your subscription)
    redpen history               past verdicts from the ledger

The default path is deterministic, stays under the speed budget, and makes no
network calls except the explicit git-remote / gh probes. --deep adds headless
`claude -p` calls that run on the user's own Claude Code subscription.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import config, ledger
from .claims import claims_from_transcript, extract_claims, load_transcript_for
from .config import TOOL_NAME
from .engine import tally, verify
from .probes.base import ProbeContext, Verdict
from .render import Palette, _supports_color, render_audit, render_header, render_history, render_report
from .util import run


def find_project_root(cwd: Path) -> Path:
    """Git top-level if we're in a repo, else the cwd."""
    rc, out, _ = run(["git", "rev-parse", "--show-toplevel"], cwd=cwd)
    if rc == 0 and out.strip():
        return Path(out.strip())
    return cwd


def _color_choice(no_color: bool) -> bool | None:
    return False if no_color else None


def cmd_check(args: argparse.Namespace) -> int:
    start = time.perf_counter()
    cwd = Path.cwd()
    show_art = not args.no_art
    color = _color_choice(args.no_color)

    # Always load the transcript: several probes (tests_pass, exit_code_scan,
    # todos_remaining) judge against it even for an ad-hoc question. A
    # --transcript override bypasses auto-discovery (handy for CI and fixtures).
    if args.transcript:
        tpath = Path(args.transcript)
        if not tpath.is_file():
            print(f"{TOOL_NAME}: transcript not found: {tpath}", file=sys.stderr)
            return 2
        from .transcript import parse_transcript

        transcript = parse_transcript(tpath)
    else:
        transcript = load_transcript_for(cwd)

    # --deep engages the LLM judge on UNVERIFIABLE claims (deterministic probes
    # still run first). The judge sees only gathered evidence, never the codebase.
    judge = None
    if args.deep and os.environ.get("REDPEN_HOOK"):
        # Recursion guard: the auto-verify Stop hook must never spawn `claude -p`.
        print(f"{TOOL_NAME}: --deep is disabled inside the auto-verify hook; running deterministic only.\n")
    elif args.deep:
        if config.ENABLE_LLM:
            from .judge import make_judge

            judge = make_judge()
            print(f"{TOOL_NAME}: --deep — LLM judge ({config.LLM_MODEL}) weighs in on unverifiable claims.\n")
        else:
            print(f"{TOOL_NAME}: --deep requested but ENABLE_LLM is off; running deterministic only.\n")

    # Surface which transcript fed the run when it matters (deep audit / override).
    if transcript is not None and transcript.path and (args.deep or args.transcript):
        p_src = Palette(_supports_color(sys.stdout) if color is None else color)
        print(p_src.dim(f"transcript: {transcript.path}"))

    if args.question:
        claims = extract_claims(args.question, source="adhoc")
    elif transcript is not None:
        claims = claims_from_transcript(transcript)
    else:
        claims = []

    if not claims:
        p = Palette(_supports_color(sys.stdout) if color is None else color)
        header = render_header(show_art, color)
        if header:
            print(header)
            print()
        if transcript is None and not args.question:
            print(p.dim(f"No Claude Code transcript found for {cwd}. Nothing to grade."))
        else:
            print(p.dim("No completion claims found. Nothing to grade."))
        return 0

    ctx = ProbeContext(cwd=cwd, run=args.run, transcript=transcript)
    findings = verify(claims, ctx, judge=judge)
    elapsed = time.perf_counter() - start

    session_id = transcript.session_id if transcript else ("adhoc" if args.question else "")
    project_root = find_project_root(cwd)
    try:
        ledger.record(project_root, findings, session_id=session_id)
    except Exception as exc:  # noqa: BLE001 -- the ledger must never break a check
        print(f"{TOOL_NAME}: warning: could not write ledger ({exc})", file=sys.stderr)

    # Full-request audit (deep only): reconcile what was asked -> what Claude
    # claimed -> what the evidence + judge actually show. Two extra LLM calls.
    audit: list[dict] = []
    if judge is not None and transcript is not None and transcript.final_user_text:
        from .claims import assistant_statements
        from .judge import audit_request

        # "claimed" = what the assistant actually said it did (all its
        # statements), so the audit can spot a claim with no backing probe,
        # falling back to the probe-matched claim texts if the message is bare.
        claimed = assistant_statements(transcript) or list(dict.fromkeys(c.text for c in claims))
        verdicts = [(f.display, f.result.verdict.value, f.result.detail) for f in findings]
        # One call: decompose the request AND reconcile it against the evidence.
        audit = audit_request(transcript.final_user_text, claimed, verdicts)

    if args.json:
        print(_as_json(findings, elapsed, audit))
    else:
        print(render_report(findings, show_art=show_art, color=color, elapsed=elapsed))
        if audit:
            print()
            print(render_audit(audit, color=color))

    counts = tally(findings)
    return 1 if counts[Verdict.FAIL] else 0


def _as_json(findings, elapsed: float, audit: list[dict] | None = None) -> str:
    counts = tally(findings)
    payload = {
        "tool": TOOL_NAME,
        "elapsed_seconds": round(elapsed, 4),
        "summary": {v.value: counts[v] for v in Verdict},
        "findings": [
            {
                "claim": f.claim_text,
                "subject": f.display,
                "source": f.source,
                "probe": f.result.probe,
                "verdict": f.result.verdict.value,
                "detail": f.result.detail,
                "evidence": f.result.evidence,
            }
            for f in findings
        ],
        "request_audit": audit or [],
    }
    return json.dumps(payload, indent=2, default=str)


def cmd_history(args: argparse.Namespace) -> int:
    project_root = find_project_root(Path.cwd())
    rows = ledger.history(project_root, limit=args.limit)
    print(render_history(rows, color=_color_choice(args.no_color)))
    return 0


def cmd_install_hook(args: argparse.Namespace) -> int:
    from .hook import install_hook

    root = find_project_root(Path.cwd())
    changed, info = install_hook(root)
    if changed:
        print(f"{TOOL_NAME}: auto-verify Stop hook installed in {info}")
        print(f"{TOOL_NAME}: it runs `redpen check` (deterministic only) after each task.")
        print(f"{TOOL_NAME}: remove it anytime with `redpen uninstall-hook`.")
    else:
        print(f"{TOOL_NAME}: nothing to do ({info}).")
    return 0


def cmd_uninstall_hook(args: argparse.Namespace) -> int:
    from .hook import uninstall_hook

    root = find_project_root(Path.cwd())
    changed, info = uninstall_hook(root)
    if changed:
        print(f"{TOOL_NAME}: auto-verify Stop hook removed from {info}")
    else:
        print(f"{TOOL_NAME}: nothing to remove ({info}).")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="redpen",
        description=f"{TOOL_NAME} — verify Claude Code's completion claims against reality.",
    )
    sub = parser.add_subparsers(dest="command")

    check = sub.add_parser("check", help="verify completion claims")
    check.add_argument("question", nargs="?", default=None, help="an ad-hoc claim to verify, e.g. \"is the push done?\"")
    check.add_argument("--run", action="store_true", help="permit re-running tests/build/lint (default: read-only)")
    check.add_argument("--transcript", metavar="PATH", default=None, help="read this transcript instead of auto-discovering one")
    check.add_argument("--no-art", action="store_true", help="suppress the mascot")
    check.add_argument("--deep", action="store_true", help="(Phase 2) LLM judgement layer")
    check.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    check.add_argument("--no-color", action="store_true", help="disable ANSI color")

    hist = sub.add_parser("history", help="show past verdicts from the ledger")
    hist.add_argument("--limit", type=int, default=20, help="how many rows to show")
    hist.add_argument("--no-color", action="store_true", help="disable ANSI color")

    sub.add_parser("install-hook", help="install the opt-in auto-verify Stop hook (deterministic only)")
    sub.add_parser("uninstall-hook", help="remove the auto-verify Stop hook")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "check":
        return cmd_check(args)
    if args.command == "history":
        return cmd_history(args)
    if args.command == "install-hook":
        return cmd_install_hook(args)
    if args.command == "uninstall-hook":
        return cmd_uninstall_hook(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

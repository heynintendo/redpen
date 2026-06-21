"""RedPen command-line interface.

    redpen check                 auto: extract claims from the latest transcript
    redpen check "<question>"    verify one ad-hoc claim
    redpen check --run           re-run tests/build/lint (off by default; flaky, side-effecting)
    redpen check --deep          LLM judge + full-request audit (uses your subscription)
    redpen explain <n>           full evidence behind verdict #n from the last run
    redpen baseline              snapshot task-start state for session-scoping
    redpen history               past verdicts from the ledger

The default path is deterministic, never re-runs or executes anything, stays
under the speed budget, and makes no network calls except the explicit
git-remote / gh probes. Test/build/lint verdicts come from the transcript;
--run is an explicit last resort. --deep adds headless `claude -p` calls.
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
from .probes.base import ProbeContext, ProbeSpec, Verdict
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
    project_root = find_project_root(cwd)
    show_art = not args.no_art
    color = _color_choice(args.no_color)

    # Always load the transcript: several probes (tests_pass, exit_code_scan,
    # todos_remaining) judge against it even for an ad-hoc question. A
    # --transcript override bypasses auto-discovery (handy for CI and fixtures).
    from .transcript import discover_transcript, parse_transcript

    discovery = None
    if args.transcript:
        tpath = Path(args.transcript)
        if not tpath.is_file():
            print(f"{TOOL_NAME}: transcript not found: {tpath}", file=sys.stderr)
            return 2
        transcript = parse_transcript(tpath)
    else:
        discovery = discover_transcript(cwd)
        transcript = parse_transcript(discovery.path) if discovery.path else None

    # --deep engages the LLM judge on UNVERIFIABLE claims (deterministic probes
    # still run first). The judge sees only gathered evidence, never the codebase.
    judge = None
    if args.deep and os.environ.get("REDPEN_HOOK"):
        # Recursion guard: the auto-verify Stop hook must never spawn `claude -p`.
        print(f"{TOOL_NAME}: --deep is disabled inside the auto-verify hook; running deterministic only.\n")
    elif args.deep:
        if config.ENABLE_LLM:
            from .judge import make_judge

            judge = make_judge(cache_dir=project_root / config.LEDGER_DIR)
            print(f"{TOOL_NAME}: --deep — LLM judge ({config.LLM_MODEL}) weighs in on unverifiable claims.\n")
        else:
            print(f"{TOOL_NAME}: --deep requested but ENABLE_LLM is off; running deterministic only.\n")

    # Surface which transcript fed the run when it matters: the deep audit, an
    # explicit override, or -- the fail-safe -- when discovery couldn't confirm
    # this is the current session (several transcripts, no session-id match), so
    # the user is never silently shown the wrong session's verdict.
    if transcript is not None and transcript.path:
        p_src = Palette(_supports_color(sys.stdout) if color is None else color)
        ambiguous = discovery is not None and discovery.ambiguous
        if args.deep or args.transcript or ambiguous:
            print(p_src.dim(f"transcript: {transcript.path}"))
        if ambiguous:
            print(p_src.dim(
                "note: couldn't confirm this is the current session "
                "(several transcripts found) — pass --transcript <path> to be sure"
            ))

    if args.question:
        claims = extract_claims(args.question, source="adhoc")
    elif transcript is not None:
        claims = claims_from_transcript(transcript)
    else:
        claims = []

    # Git is one optional evidence source. In a non-git folder, omit the
    # generic git probes (no noise); explicit git claims stay and FAIL.
    from .changeset import is_git_repo

    is_git = is_git_repo(project_root)
    from .claims import drop_inapplicable_git_probes

    claims = drop_inapplicable_git_probes(claims, is_git)

    if not claims:
        p = Palette(_supports_color(sys.stdout) if color is None else color)
        header = render_header(show_art, color)
        if header:
            print(header)
            print()
        if transcript is None and not args.question:
            print(p.dim("Nothing to grade — I couldn't find a Claude Code session for this folder."))
        else:
            print(p.dim("Nothing to grade — the session didn't actually claim to finish anything I can check."))
        return 0

    # Build the session changed-set once (transcript + git delta vs baseline)
    # and share it via the context so session-scoping probes don't rebuild it.
    from .baseline import read_baseline
    from .changeset import build_changed_set

    baseline = read_baseline(project_root)
    changed_set = build_changed_set(project_root, transcript=transcript, baseline=baseline)

    # Custom rules (.redpen.yml): each claim hits the first matching rule, which
    # contributes a gated custom_rule probe. The main lever for stack-specific
    # claims that built-in probes would otherwise leave UNVERIFIABLE.
    from .customrules import load_rules, match_rule

    rules = load_rules(project_root)
    if rules:
        for claim in claims:
            rule = match_rule(rules, claim.text)
            if rule is not None:
                claim.probe_specs.append(
                    ProbeSpec("custom_rule", {"rule": rule.as_dict()}, label=rule.name or f"rule: {rule.command[:30]}")
                )

    ctx = ProbeContext(
        cwd=cwd, run=args.run, transcript=transcript, changed_set=changed_set, baseline=baseline
    )
    findings = verify(claims, ctx, judge=judge)
    elapsed = time.perf_counter() - start

    session_id = transcript.session_id if transcript else ("adhoc" if args.question else "")
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

    # Persist the full run so `redpen explain <n>` can audit any verdict line.
    from .lastrun import save_last_run

    try:
        save_last_run(project_root, findings, session_id=session_id, audit=audit, elapsed=elapsed)
    except Exception as exc:  # noqa: BLE001 -- never let persistence break a check
        print(f"{TOOL_NAME}: warning: could not write last_run.json ({exc})", file=sys.stderr)

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


def cmd_explain(args: argparse.Namespace) -> int:
    from .lastrun import load_last_run
    from .render import render_explain

    project_root = find_project_root(Path.cwd())
    data = load_last_run(project_root)
    color = _color_choice(args.no_color)
    p = Palette(_supports_color(sys.stdout) if color is None else color)
    if not data or not data.get("findings"):
        print(p.dim("No recorded run. Run `redpen check` first."))
        return 0

    findings = data["findings"]
    which = (args.which or "").strip().lower()

    # No argument: list the numbered verdicts so the user can pick one.
    if not which:
        print(p.bold(f"{TOOL_NAME} — last run ({len(findings)} verdicts). explain <n> for detail:"))
        print()
        sym = {"OK": p.green("✓"), "FAIL": p.red("✗")}
        for f in findings:
            mark = sym.get(f["verdict"], p.yellow("⚠"))
            print(f"  {p.dim(str(f['n']) + '.'):>4} {mark}  {f.get('subject', '')}")
        return 0

    if which == "last":
        record = findings[-1]
    else:
        try:
            n = int(which)
        except ValueError:
            print(p.dim(f"Not a verdict number: {args.which}. Use `redpen explain <n>` or `explain last`."))
            return 2
        match = [f for f in findings if f.get("n") == n]
        if not match:
            print(p.dim(f"No verdict #{n} in the last run (it had {len(findings)})."))
            return 2
        record = match[0]

    print(render_explain(record, color=color))
    return 0


def cmd_baseline(args: argparse.Namespace) -> int:
    from .baseline import write_baseline

    root = find_project_root(Path.cwd())
    path = write_baseline(root)
    if not args.quiet:
        print(f"{TOOL_NAME}: baseline snapshot written to {path}")
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
    check.add_argument("--run", action="store_true", help="re-run tests/build/lint and custom rules (off by default; flaky, side-effecting)")
    check.add_argument("--transcript", metavar="PATH", default=None, help="read this transcript instead of auto-discovering one")
    check.add_argument("--no-art", action="store_true", help="suppress the mascot")
    check.add_argument("--deep", action="store_true", help="(Phase 2) LLM judgement layer")
    check.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    check.add_argument("--no-color", action="store_true", help="disable ANSI color")

    explain = sub.add_parser("explain", help="show the full evidence behind a verdict from the last run")
    explain.add_argument("which", nargs="?", default=None, help="the verdict number, or 'last' (omit to list them)")
    explain.add_argument("--no-color", action="store_true", help="disable ANSI color")

    hist = sub.add_parser("history", help="show past verdicts from the ledger")
    hist.add_argument("--limit", type=int, default=20, help="how many rows to show")
    hist.add_argument("--no-color", action="store_true", help="disable ANSI color")

    base = sub.add_parser("baseline", help="snapshot the task-start state (HEAD + status + hashes) for session-scoping")
    base.add_argument("--quiet", action="store_true", help="print nothing on success")

    sub.add_parser("install-hook", help="install the opt-in auto-verify hooks (deterministic only)")
    sub.add_parser("uninstall-hook", help="remove the auto-verify hooks")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "check":
        return cmd_check(args)
    if args.command == "explain":
        return cmd_explain(args)
    if args.command == "baseline":
        return cmd_baseline(args)
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

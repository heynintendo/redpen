"""The contradiction engine: scan the agent's OWN captured tool output for
failure signatures.

This is the highest-precision FAIL in RedPen -- it needs no re-execution. When a
summary claim ("tests pass", "build succeeds", "done") is contradicted by a
failure the agent itself printed, we quote the contradicting line.

Precision rule (absolute): a FAIL fires only on a real, relevant, final-state
failure. Concretely:

  * A failure SIGNATURE (a "=== 1 failed", "Traceback", "error TS2304", ...) in
    the output counts only when it comes from a command that EXECUTES work --
    not a `cat`/`echo`/`head` that merely prints or reads a file, where such a
    string is content, not a failure.
  * A bare NON-ZERO EXIT (no failure signature) counts only when the command is
    a recognized work runner relevant to the claim. A benign `grep` no-match,
    `git diff --quiet`, the `test -f` builtin, or a tool that exits non-zero by
    design is normal, not a failure.
  * FINAL -- when the same command ran more than once, only its LAST run counts.
    A test that failed early and passed on re-run is a pass.

Shared by ``contradiction_scan`` (default suite) and by ``tests_pass`` /
``build_ok`` so they can emit incontestable, quoted FAILs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Output signatures, by claim kind. Kept specific: a FAIL fires only when one of
# these matches a *runner's* captured output. "0 failed" / "no failures" must NOT
# match -- the failure-count patterns require a non-zero count.
_TEST_SIGNATURES = [
    re.compile(r"(?mi)^=+ .*\b[1-9]\d* failed"),     # pytest "=== 1 failed, 2 passed ===" (not "0 failed")
    re.compile(r"(?m)^FAILED\b"),                     # pytest FAILED <nodeid>
    re.compile(r"\bAssertionError\b"),
    re.compile(r"(?i)\b[1-9]\d* (?:tests? )?failing\b"),  # mocha/jest "3 failing" (not "0 failing")
    re.compile(r"(?mi)^Tests:.*\b[1-9]\d* failed"),   # jest summary line
    re.compile(r"\btest result: FAILED\b"),           # cargo test
    re.compile(r"(?m)^--- FAIL[: ]"),                 # go test per-test failure
    re.compile(r"(?m)^FAIL\b"),                       # go test summary / jest "FAIL <suite>"
]
_BUILD_SIGNATURES = [
    re.compile(r"\bBUILD FAILED\b"),
    re.compile(r"(?i)\berror TS\d{2,5}\b"),           # tsc
    re.compile(r"(?i)\bcompilation (?:error|failed)\b"),
    re.compile(r"(?m)^error\[E\d+\]"),                # rustc
    re.compile(r"(?m)^.+:\d+:\d+: error:"),           # gcc/clang/file:line:col: error:
]
_GENERIC_SIGNATURES = [
    re.compile(r"Traceback \(most recent call last\)"),
    re.compile(r"(?mi)^error:"),
]

_SIGNATURES = {
    "tests": _TEST_SIGNATURES,
    "build": _BUILD_SIGNATURES,
    "any": _GENERIC_SIGNATURES + _TEST_SIGNATURES + _BUILD_SIGNATURES,
}

# Commands that merely print or read a file. A failure signature in THEIR output
# is content (a doc dump, a quoted log line), never a real failure, so we don't
# scan it.
_DISPLAY_CMDS = frozenset(
    "cat echo printf head tail less more bat tac nl type view fold column "
    "base64 xxd od hexdump strings".split()
)


def _is_display_command(cmd: str) -> bool:
    # Peel leading subshell / group openers so `(cat x)` and `{ cat x; }` are
    # still recognized as display commands.
    s = cmd.strip().lstrip("(){} \t")
    parts = s.split()
    if not parts:
        return False
    return parts[0].rsplit("/", 1)[-1] in _DISPLAY_CMDS


# Recognized work runners, by kind. A *bare* non-zero exit (with no failure
# signature in the output) is a contradiction only for one of these -- so a
# benign grep/diff/`test -f`/custom-script exit is never a failure. (Output
# signatures, by contrast, count for any executing command, runner or not.)
_TEST_RUNNERS = (
    "pytest", "py.test", "python -m pytest", "python -m unittest",
    "npm test", "npm run test", "yarn test", "pnpm test", "jest", "vitest",
    "mocha", "cargo test", "go test", "make test", "rake test", "rspec",
    "phpunit", "gradle test", "mvn test", "dotnet test", "tox",
)
_BUILD_RUNNERS = (
    "npm run build", "yarn build", "pnpm build", "make build", "cargo build",
    "cargo check", "go build", "tsc", "webpack", "vite build", "next build",
    "rollup", "esbuild", "gradle build", "mvn package", "mvn compile",
    "dotnet build", "cmake --build",
)
_RUNNERS = {
    "tests": _TEST_RUNNERS,
    "build": _BUILD_RUNNERS,
    "any": _TEST_RUNNERS + _BUILD_RUNNERS,
}


@dataclass
class Failure:
    """A failure signature found in a runner's own captured output."""

    command: str
    line: str
    via: str  # "output" (a signature matched) or "exit" (nonzero exit code)


def _line_at(text: str, pos: int) -> str:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text[start: end if end != -1 else len(text)].strip()


def _is_runner(cmd: str, runners) -> bool:
    """True if ``cmd`` matches a recognized runner token as a command word."""
    hay = cmd.lower()
    for tok in runners:
        # Match as a command word: at start or after a shell separator, and not
        # glued to more word characters (so 'tsc' != 'tsconfig', the `test`
        # builtin won't match the 'go test' runner token, etc.).
        if re.search(r"(?:^|[\s;&|/`(])" + re.escape(tok) + r"(?![\w.])", hay):
            return True
    return False


def _failure_line(event, sigs, runners) -> tuple[str | None, str | None]:
    """A quotable failure line for an event, or (None, None) if it's clean.

    An output signature (real failure text) counts for any executing command but
    NOT for a display/read command. A bare non-zero exit counts only for a
    recognized work runner.
    """
    out = event.output or ""
    cmd = (event.command or event.label or "")
    if not _is_display_command(cmd):
        for pat in sigs:
            m = pat.search(out)
            if m:
                return _line_at(out, m.start())[:200], "output"
    if event.failed and _is_runner(cmd, runners):
        tail = [ln for ln in out.strip().splitlines() if ln.strip()]
        return (tail[-1][:200] if tail else f"{event.label} exited non-zero"), "exit"
    return None, None


def _cmd_key(event) -> str:
    return " ".join((event.command or event.label or "").split()).lower()


def find_failures(events, kind: str = "any") -> list[Failure]:
    """Real failures across the tool events, relevant to ``kind``.

    Last-run-aware: for each distinct command only its LAST run is judged, so a
    fail-then-pass re-run is a pass. Benign commands, benign output, and benign
    non-zero exits never produce a failure (see the precision rule above).
    """
    runners = _RUNNERS.get(kind, _RUNNERS["any"])
    sigs = _SIGNATURES.get(kind, _SIGNATURES["any"])

    last: dict[str, object] = {}
    order: list[str] = []
    for ev in events:
        key = _cmd_key(ev)
        if not key:
            continue
        if key not in last:
            order.append(key)
        last[key] = ev  # later run overwrites the earlier one -> last run wins

    out: list[Failure] = []
    for key in order:
        ev = last[key]
        line, via = _failure_line(ev, sigs, runners)
        if line:
            out.append(Failure(command=(ev.command or ev.label)[:200], line=line, via=via))
    return out

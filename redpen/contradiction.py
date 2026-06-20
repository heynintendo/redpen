"""The contradiction engine: scan the agent's OWN captured tool output for
failure signatures.

This is the highest-precision FAIL in RedPen -- it needs no re-execution. When a
summary claim ("tests pass", "build succeeds", "done") is contradicted by a
failure the agent itself printed, we quote the contradicting line.

Shared by ``contradiction_scan`` (default suite) and by ``tests_pass`` /
``build_ok`` so they can emit incontestable, quoted FAILs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Output signatures, by claim kind. Kept specific: a FAIL fires only when one of
# these matches the agent's captured output, so over-broad patterns would cost
# precision. (A matched line is quoted back verbatim.)
_TEST_SIGNATURES = [
    re.compile(r"(?mi)^=+ .*\b\d+ failed"),       # pytest "=== 1 failed, 2 passed ==="
    re.compile(r"(?m)^FAILED\b"),                  # pytest FAILED <nodeid>
    re.compile(r"\bAssertionError\b"),
    re.compile(r"(?i)\b\d+ (?:tests? )?failing\b"),  # mocha/jest "3 failing"
    re.compile(r"(?mi)^Tests:.*\b\d+ failed"),     # jest summary line
    re.compile(r"\btest result: FAILED\b"),         # cargo test
]
_BUILD_SIGNATURES = [
    re.compile(r"\bBUILD FAILED\b"),
    re.compile(r"(?i)\berror TS\d{2,5}\b"),         # tsc
    re.compile(r"(?i)\bcompilation (?:error|failed)\b"),
    re.compile(r"(?m)^error\[E\d+\]"),              # rustc
    re.compile(r"(?m)^.+:\d+:\d+: error:"),         # gcc/clang/file:line:col: error:
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

# Keywords that make a *failed-exit* event relevant to a specific claim kind.
_RELEVANT = {
    "tests": ("pytest", "test", "jest", "mocha", "vitest", "cargo test", "go test"),
    "build": ("build", "compile", "tsc", "cargo build", "go build", "make", "webpack"),
}


@dataclass
class Failure:
    """A failure signature found in the agent's own captured output."""

    command: str
    line: str
    via: str  # "output" (a signature matched) or "exit" (nonzero exit code)


def _line_at(text: str, pos: int) -> str:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text[start: end if end != -1 else len(text)].strip()


def _relevant_to(event, kind: str) -> bool:
    if kind == "any":
        return True
    hay = f"{event.command} {event.label}".lower()
    return any(k in hay for k in _RELEVANT.get(kind, ()))


def find_failures(events, kind: str = "any") -> list[Failure]:
    """Failure signatures across tool events, relevant to ``kind``."""
    sigs = _SIGNATURES.get(kind, _SIGNATURES["any"])
    out: list[Failure] = []
    for ev in events:
        cmd = ev.command or ev.label
        matched = None
        for pat in sigs:
            m = pat.search(ev.output or "")
            if m:
                matched = _line_at(ev.output, m.start())
                break
        if matched:
            out.append(Failure(command=cmd, line=matched[:200], via="output"))
        elif ev.failed and _relevant_to(ev, kind):
            tail = [ln for ln in (ev.output or "").strip().splitlines() if ln.strip()]
            line = tail[-1][:200] if tail else f"{ev.label} exited non-zero"
            out.append(Failure(command=cmd, line=line, via="exit"))
    return out

"""Transcript-derived probes: contradiction_scan (a.k.a. exit_code_scan).

Scans the agent's OWN captured tool output for failure signatures. When a
summary success claim is contradicted by a failure the agent itself printed, we
emit FAIL quoting the line -- the highest-precision verdict RedPen has, needing
no re-execution.
"""

from __future__ import annotations

from ..contradiction import find_failures
from ..patterns import narrates_success
from .base import ProbeContext, ProbeResult, fail, ok, unverifiable


def contradiction_scan(ctx: ProbeContext, kind: str = "any", **_: object) -> ProbeResult:
    """Catch a failure in the agent's captured output narrated as success.

    FAIL only on the genuine contradiction: a failure signature in the captured
    output AND a final message that narrates success. A failure with no success
    narration is UNVERIFIABLE; no failures at all is OK (a clean session).
    """
    t = ctx.transcript
    if t is None:
        return unverifiable("contradiction_scan", "no session transcript available")

    failures = find_failures(t.tool_events, kind)
    if not failures:
        return ok(
            "contradiction_scan",
            f"no failure signatures across {len(t.tool_events)} tool result(s)",
            contradictions=[],
            total=len(t.tool_events),
        )

    quotes = [{"command": f.command[:120], "line": f.line, "via": f.via} for f in failures[:10]]
    final = t.final_assistant_text or ""
    if narrates_success(final):
        worst = failures[-1]
        return fail(
            "contradiction_scan",
            f"agent claimed success but its own output shows failure: {worst.line[:70]}",
            contradiction=worst.line,
            command=worst.command[:200],
            contradictions=quotes,
        )

    return unverifiable(
        "contradiction_scan",
        f"{len(failures)} failure signature(s) in tool output, but no success was narrated over them",
        contradictions=quotes,
    )


# Back-compat: the original name. The default suite and earlier tests use it.
exit_code_scan = contradiction_scan

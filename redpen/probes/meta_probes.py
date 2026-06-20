"""Meta probes that don't inspect the system, only the claim itself."""

from __future__ import annotations

from .base import ProbeContext, ProbeResult, unverifiable


def unmapped(ctx: ProbeContext, **_: object) -> ProbeResult:
    """A claim that maps to no deterministic probe.

    Surfaced (never dropped) so the user sees there's an assertion RedPen can't
    check. Absent evidence -> UNVERIFIABLE, never FAIL.
    """
    return unverifiable("unmapped", "no probe for this claim -- verify manually")

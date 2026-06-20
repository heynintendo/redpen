"""The verdict pipeline: run each claim's probes and collect findings.

This is where the Phase 2 architecture seam lives. ``verify`` accepts an
optional ``judge`` callable. In Phase 1 nothing passes one in, so the pipeline
is purely deterministic. The seam is real, not cosmetic: when a judge is
supplied it is handed ONLY the evidence a probe already gathered -- never the
codebase -- and may upgrade an UNVERIFIABLE into OK/FAIL.
"""

from __future__ import annotations

from dataclasses import dataclass

from .claims import Claim
from .probes import PROBES
from .probes.base import ProbeContext, ProbeResult, ProbeSpec, Verdict
from .util import start_recording, stop_recording


@dataclass
class Finding:
    """A single (claim, probe) outcome, ready to render and to log."""

    claim_text: str
    source: str
    result: ProbeResult
    label: str | None = None

    @property
    def display(self) -> str:
        """The short subject shown on the verdict line."""
        return self.label or self.claim_text


def run_probe(spec: ProbeSpec, ctx: ProbeContext) -> ProbeResult:
    """Run one probe by name, converting any crash into UNVERIFIABLE.

    A probe that raises must never take down the run -- and it must never be
    reported as FAIL (that would be crying wolf on absent evidence).
    """
    fn = PROBES.get(spec.name)
    if fn is None:
        return ProbeResult(spec.name, Verdict.UNVERIFIABLE, f"unknown probe: {spec.name}")
    start_recording()  # capture the exact commands this probe runs (for `explain`)
    try:
        result = fn(ctx, **spec.kwargs)
    except Exception as exc:  # noqa: BLE001 -- deliberately defensive
        result = ProbeResult(spec.name, Verdict.UNVERIFIABLE, f"probe errored: {exc}")
    finally:
        commands = stop_recording()
    if commands and "commands" not in result.evidence:
        result.evidence["commands"] = commands
    return result


def verify(claims: list[Claim], ctx: ProbeContext, judge=None) -> list[Finding]:
    """Run every probe for every claim and collect findings.

    ``judge`` is the Phase 2 LLM seam. See redpen/judge.py for the contract.
    """
    findings: list[Finding] = []
    for claim in claims:
        for spec in claim.probe_specs:
            result = run_probe(spec, ctx)

            # --- Phase 2 judge --------------------------------------------
            # An optional judge may refine an UNVERIFIABLE result using ONLY
            # the evidence already gathered (it never reads the codebase).
            # Deterministic OK/FAIL is trusted and never sent to the judge.
            if judge is not None and result.verdict is Verdict.UNVERIFIABLE:
                refined = judge(claim.text, result)  # judge(claim, result) -> ProbeResult
                if refined is not None:
                    result = refined
            # --------------------------------------------------------------

            findings.append(
                Finding(claim_text=claim.text, source=claim.source, result=result, label=spec.label)
            )
    return findings


def tally(findings: list[Finding]) -> dict[Verdict, int]:
    counts = {Verdict.OK: 0, Verdict.FAIL: 0, Verdict.UNVERIFIABLE: 0}
    for f in findings:
        counts[f.result.verdict] += 1
    return counts

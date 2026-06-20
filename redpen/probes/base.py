"""Shared types for the probe library.

Every probe is a small, self-contained function with the signature::

    probe(ctx: ProbeContext, **kwargs) -> ProbeResult

It gathers *targeted* evidence (never re-reading the whole codebase) and judges
it into exactly one of three verdicts. The ``evidence`` dict is deliberately
structured: in Phase 2 an LLM judge will look at ``evidence`` alone -- it never
sees the codebase -- so anything a downstream judge might need belongs there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # avoid an import cycle; transcript imports nothing from here
    from redpen.changeset import ChangedSet
    from redpen.transcript import Transcript


class Verdict(str, Enum):
    """Exactly three verdicts. Nothing else exists.

    OK            -- evidence substantiates the claim.
    FAIL          -- evidence *contradicts* the claim.
    UNVERIFIABLE  -- evidence is absent or inconclusive; we cannot tell.

    The cardinal rule: emit FAIL only on contradiction, never on mere absence.
    Absence is UNVERIFIABLE. A verifier that cries wolf is worthless.
    """

    OK = "OK"
    FAIL = "FAIL"
    UNVERIFIABLE = "UNVERIFIABLE"


@dataclass
class ProbeResult:
    """The structured result of a single probe."""

    probe: str
    verdict: Verdict
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProbeContext:
    """Everything a probe is allowed to look at.

    Probes get the project root, whether re-running side-effecting commands is
    permitted (``--run``), and the parsed session transcript (if one exists).
    They must not wander beyond this -- targeted evidence only.
    """

    cwd: Path
    run: bool = False
    transcript: "Transcript | None" = None
    # Session changed-set (what the agent actually touched) and the task-start
    # baseline. Built once in the CLI and shared; None when unavailable.
    changed_set: "ChangedSet | None" = None
    baseline: dict | None = None

    def resolve(self, path: str) -> Path:
        """Resolve a possibly-relative path against the project root."""
        p = Path(path)
        return p if p.is_absolute() else (self.cwd / p)


# --- verdict constructors ---------------------------------------------------
# Probes call these so the probe name is attached consistently and the call
# sites read like prose: ``return ok("git_pushed", "no unpushed commits")``.


def ok(probe: str, detail: str, **evidence: Any) -> ProbeResult:
    return ProbeResult(probe, Verdict.OK, detail, dict(evidence))


def fail(probe: str, detail: str, **evidence: Any) -> ProbeResult:
    return ProbeResult(probe, Verdict.FAIL, detail, dict(evidence))


def unverifiable(probe: str, detail: str, **evidence: Any) -> ProbeResult:
    return ProbeResult(probe, Verdict.UNVERIFIABLE, detail, dict(evidence))


@dataclass
class ProbeSpec:
    """A probe to run, plus its arguments. Produced by the claim extractor.

    ``label`` is an optional short subject for the verdict line (used when a
    probe comes from a generic "done" claim and the raw sentence would be a
    poor per-line label). When None, the renderer falls back to the claim text.
    """

    name: str
    kwargs: dict[str, Any] = field(default_factory=dict)
    label: str | None = None

    def key(self) -> tuple:
        return (self.name, tuple(sorted(self.kwargs.items())))

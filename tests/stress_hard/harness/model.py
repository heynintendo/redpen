"""Ground-truth model for the stress_hard suite.

Every case carries *programmatic ground truth*: for each claim RedPen is expected
to surface, we record (a) the probe it should map to, (b) the verdicts that are
contract-acceptable, and (c) the reality of the claim -- whether it genuinely
holds, is a genuine lie, or is unknowable.

From that we classify any divergence into exactly the three buckets the user
cares about, plus a soft bucket for non-headline contract misses:

    false_FAIL  -- RedPen emitted FAIL when the claim is true / a user-or-other-
                   session edit / merely unprovable. The cardinal sin: crying
                   wolf. (actual == FAIL and reality is not False.)

    false_OK    -- RedPen emitted OK for a genuine lie. (actual == OK and
                   reality is False.)

    misparse    -- a claim-extraction error: a phantom finding RedPen invented
                   for something that was not claimed, or a finding it failed to
                   produce for something that was claimed.

    soft        -- the verdict was outside the acceptable set but is neither of
                   the two headline errors (e.g. UNVERIFIABLE where OK was
                   ideal, or a missed contradiction). Counts against pass rate,
                   not against the headline tallies.

These are independent dimensions, not a partition: a phantom FAIL increments
both misparse and false_FAIL. The report states this explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

OK = "OK"
FAIL = "FAIL"
UNV = "UNVERIFIABLE"
VERDICTS = (OK, FAIL, UNV)


@dataclass(frozen=True)
class EF:
    """One expected finding: a (claim -> probe) outcome with ground truth.

    ``reality`` is the ground truth of the underlying claim:
      True  -- the claim genuinely holds (a FAIL here is crying wolf)
      False -- the claim is a genuine lie (an OK here is a rubber stamp)
      None  -- unknowable from the evidence (only UNVERIFIABLE is correct)

    ``accept`` is the set of verdicts that satisfy RedPen's contract for this
    finding. It governs case pass/fail and the soft bucket; the headline
    false_FAIL / false_OK tallies are driven by ``reality`` so they mean exactly
    what the user asked for.
    """

    probe: str
    accept: frozenset
    reality: Optional[bool]
    subject_substr: str = ""
    note: str = ""


def ef(
    probe: str,
    *,
    true: Optional[bool] = None,
    accept: Optional[set] = None,
    subject: str = "",
    note: str = "",
) -> EF:
    """Build an EF, deriving a sensible ``accept`` set from ``true`` when omitted.

        true=True   -> accept {OK, UNVERIFIABLE}   (must never FAIL)
        true=False  -> accept {FAIL, UNVERIFIABLE} (may catch or miss; never OK)
        true=None   -> accept {UNVERIFIABLE}       (unknowable: only UNVERIFIABLE)

    Pass ``accept`` explicitly to tighten it, e.g. accept={FAIL} to *require* a
    lie be caught, or accept={OK} to require a provable truth be confirmed.
    """
    if accept is None:
        if true is True:
            accept = {OK, UNV}
        elif true is False:
            accept = {FAIL, UNV}
        else:
            accept = {UNV}
    return EF(probe=probe, accept=frozenset(accept), reality=true, subject_substr=subject, note=note)


@dataclass
class Built:
    """The materialized inputs for one redpen invocation."""

    cwd: Path
    transcript: Optional[Path] = None
    extra_args: tuple = ()
    env: dict = field(default_factory=dict)


@dataclass
class Case:
    """One adversarial case: how to build it + its ground-truth findings."""

    cid: str
    axis: str
    title: str
    build: Callable[[Path, "object"], Built]
    efs: list
    allow_phantom: frozenset = frozenset()
    deep: bool = False
    tags: tuple = ()
    # Optional extra invariant checked against the parsed JSON / process result.
    # Signature: invariant(result_dict, returncode, stderr) -> "" if ok else reason.
    invariant: Optional[Callable] = None


@dataclass
class CaseResult:
    """The outcome of running one case, with everything the report needs."""

    cid: str
    axis: str
    title: str
    passed: bool
    false_fail: list = field(default_factory=list)
    false_ok: list = field(default_factory=list)
    misparse: list = field(default_factory=list)
    soft: list = field(default_factory=list)
    actual: list = field(default_factory=list)
    elapsed: Optional[float] = None  # redpen's own deterministic-path seconds
    wall_ms: Optional[float] = None  # subprocess wall-clock
    exit_code: Optional[int] = None
    error: str = ""

    @property
    def any_headline(self) -> bool:
        return bool(self.false_fail or self.false_ok or self.misparse)


def _match(efs: list, actuals: list):
    """Greedily match expected findings to actual findings.

    An actual finding is (probe, subject, verdict). Match by probe and, when the
    EF gives a ``subject_substr``, by substring of the actual subject.
    """
    used = [False] * len(actuals)
    pairs = []
    for e in efs:
        hit = None
        for i, a in enumerate(actuals):
            if used[i] or a["probe"] != e.probe:
                continue
            if e.subject_substr and e.subject_substr.lower() not in (a.get("subject") or "").lower():
                continue
            hit = i
            break
        if hit is None:
            pairs.append((e, None))
        else:
            used[hit] = True
            pairs.append((e, actuals[hit]))
    phantoms = [actuals[i] for i in range(len(actuals)) if not used[i]]
    return pairs, phantoms


def classify(case: Case, actuals: list) -> CaseResult:
    """Compare actual findings against the case's ground truth."""
    res = CaseResult(cid=case.cid, axis=case.axis, title=case.title, passed=True, actual=actuals)
    pairs, phantoms = _match(case.efs, actuals)

    for e, a in pairs:
        if a is None:
            res.misparse.append(
                f"MISSED claim: expected {e.probe}"
                + (f" ~'{e.subject_substr}'" if e.subject_substr else "")
                + (f" [{e.note}]" if e.note else "")
            )
            continue
        v = a["verdict"]
        subj = a.get("subject", "")
        # Headline errors, driven by ground-truth reality.
        if v == FAIL and e.reality is not False:
            res.false_fail.append(
                f"{e.probe} '{subj}' -> FAIL, but the claim is "
                f"{'true' if e.reality is True else 'unprovable (not a contradiction)'}"
                + (f"; {e.note}" if e.note else "")
            )
        elif v == OK and e.reality is False:
            res.false_ok.append(
                f"{e.probe} '{subj}' -> OK, but the claim is a genuine lie"
                + (f"; {e.note}" if e.note else "")
            )
        elif v not in e.accept:
            res.soft.append(
                f"{e.probe} '{subj}' -> {v}, acceptable={sorted(e.accept)}"
                + (f"; {e.note}" if e.note else "")
            )

    for p in phantoms:
        if p["probe"] in case.allow_phantom:
            continue
        res.misparse.append(
            f"PHANTOM finding: {p['probe']} '{p.get('subject', '')}' = {p['verdict']} "
            "(no such claim was made)"
        )
        if p["verdict"] == FAIL:
            res.false_fail.append(
                f"PHANTOM FAIL: {p['probe']} '{p.get('subject', '')}' (invented claim failed)"
            )

    res.passed = not (res.false_fail or res.false_ok or res.misparse or res.soft)
    return res

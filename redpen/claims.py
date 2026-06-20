"""Claim extractor.

A *claim* is a success assertion -- either pulled from Claude Code's latest
transcript (its final message) or asked ad-hoc by the user. Each claim is
mapped to the probe(s) that can confirm or refute it. This module never reads
the codebase; it only reads text and decides which deterministic probes to run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import patterns
from .probes.base import ProbeSpec
from .transcript import Transcript, latest_transcript_for, parse_transcript

# Short, human subjects for probes when they come from a generic "done" claim.
PROBE_SUBJECT = {
    "git_pushed": "everything is pushed",
    "git_clean": "everything is committed",
    "tests_pass": "tests pass",
    "todos_remaining": "no unfinished stubs left behind",
    "contradiction_scan": "no failed step was called done",
    "build_ok": "the build is OK",
    "lint_clean": "the linter is clean",
}


@dataclass
class Claim:
    """One success assertion and the probes that can confirm/refute it."""

    text: str
    probe_specs: list[ProbeSpec] = field(default_factory=list)
    source: str = "transcript"  # "transcript" | "adhoc"


def default_suite() -> list[ProbeSpec]:
    """The probes run for a generic "done/complete/ready" claim.

    Deliberately excludes the network probes (branch_synced, pr_status) so the
    default path stays offline and under the speed budget. Those run only when
    a claim explicitly mentions branch-sync or a PR.
    """
    names = ["git_pushed", "git_clean", "tests_pass", "todos_remaining", "contradiction_scan"]
    return [ProbeSpec(n, label=PROBE_SUBJECT.get(n)) for n in names]


def _split_sentences(text: str) -> list[str]:
    out: list[str] = []
    for line in text.splitlines():
        line = re.sub(r"^[\s>#*•\-]+", "", line)  # strip markdown bullets/headings
        line = line.strip()
        if not line:
            continue
        for piece in re.split(r"(?<=[.!?])\s+", line):
            # Strip markdown emphasis and trailing sentence punctuation so the
            # rendered claim reads cleanly ("Tests pass" not "Tests pass.").
            piece = piece.strip(" *_`").rstrip(".!?,;:")
            if piece:
                out.append(piece)
    return out


def _specs_for_sentence(s: str) -> list[ProbeSpec]:
    specs: list[ProbeSpec] = []

    for m in patterns.FILE_RE.finditer(s):
        path = m.group("path")
        # These come from create/write/add verbs, so they are creation claims:
        # require the file to be in the session changed-set, not merely to exist.
        specs.append(ProbeSpec("file_present", {"path": path, "created": True}, label=f"wrote {path}"))

    if patterns.PUSH_RE.search(s):
        specs.append(ProbeSpec("git_pushed"))
    if patterns.COMMIT_RE.search(s):
        specs.append(ProbeSpec("git_clean"))
    if patterns.mentions_tests_passing(s):
        specs.append(ProbeSpec("tests_pass"))
    if patterns.BUILD_RE.search(s):
        specs.append(ProbeSpec("build_ok"))
    if patterns.LINT_RE.search(s):
        specs.append(ProbeSpec("lint_clean"))
    if patterns.PR_RE.search(s):
        specs.append(ProbeSpec("pr_status"))
    if patterns.BRANCH_SYNC_RE.search(s):
        specs.append(ProbeSpec("branch_synced"))

    # probe-pack: dependency / type-check / test-count / symbol claims
    m = patterns.TEST_COUNT_RE.search(s)
    if m:
        specs.append(ProbeSpec("test_count", {"n": int(m.group(1))}, label=f"all {m.group(1)} tests pass"))
    if patterns.TYPECHECK_RE.search(s):
        specs.append(ProbeSpec("typecheck_clean"))
    dep = patterns.extract_dep(s)
    if dep:
        specs.append(ProbeSpec("dep_present", {"name": dep}, label=f"added dependency {dep}"))
    sym = patterns.extract_symbol(s)
    if sym:
        specs.append(ProbeSpec("symbol_exists", {"symbol": sym}, label=f"added {sym}"))

    # A generic "done/ready" claim pulls in the whole default suite.
    if patterns.DONE_RE.search(s):
        for spec in default_suite():
            specs.append(spec)

    # Catch-all: an accomplishment claim with nothing a probe can check still
    # gets surfaced as a labelled UNVERIFIABLE line, never silently dropped.
    if not specs and len(s) <= 200 and patterns.CLAIM_LIKE_RE.search(s):
        specs.append(ProbeSpec("unmapped", label=s[:80]))

    return _dedupe(specs)


def _dedupe(specs: list[ProbeSpec]) -> list[ProbeSpec]:
    seen: set = set()
    out: list[ProbeSpec] = []
    for spec in specs:
        if spec.key() not in seen:
            seen.add(spec.key())
            out.append(spec)
    return out


def _dedupe_across_claims(claims: list[Claim]) -> list[Claim]:
    """Drop a probe if an identical (name, kwargs) already ran for an earlier
    claim, so two "done"-ish sentences don't each expand the whole default suite.
    """
    seen: set = set()
    out: list[Claim] = []
    for claim in claims:
        kept = [s for s in claim.probe_specs if s.key() not in seen]
        seen.update(s.key() for s in kept)
        if kept:
            claim.probe_specs = kept
            out.append(claim)
    return out


def extract_claims(text: str, source: str = "transcript") -> list[Claim]:
    """Map a block of text to a list of (claim, probes)."""
    claims: list[Claim] = []
    for sentence in _split_sentences(text):
        specs = _specs_for_sentence(sentence)
        if specs:
            claims.append(Claim(text=sentence, probe_specs=specs, source=source))

    if claims:
        return _dedupe_across_claims(claims)

    # Nothing specific matched. Text that clearly narrates completion runs the
    # full default suite; an ad-hoc question we can't map becomes an honest
    # UNVERIFIABLE line rather than a guess or a silent drop.
    label = (text.strip() or "completion")[:120]
    if patterns.DONE_RE.search(text) or patterns.narrates_success(text):
        return [Claim(text=label, probe_specs=default_suite(), source=source)]
    if source == "adhoc":
        return [Claim(text=label, probe_specs=[ProbeSpec("unmapped", label=label[:80])], source=source)]
    return []


def claims_from_transcript(transcript: Transcript) -> list[Claim]:
    """Extract claims from a parsed transcript's final assistant message."""
    if not transcript.final_assistant_text:
        return []
    return extract_claims(transcript.final_assistant_text, source="transcript")


def load_transcript_for(cwd: Path | str, home: Path | None = None) -> Transcript | None:
    """Find and parse the latest transcript for ``cwd`` (or None)."""
    path = latest_transcript_for(cwd, home)
    if path is None:
        return None
    return parse_transcript(path)


def last_user_request(transcript: Transcript) -> str:
    """The most recent genuine human prompt in the transcript (or '')."""
    return transcript.final_user_text if transcript else ""


def assistant_statements(transcript: Transcript) -> list[str]:
    """The assistant's stated accomplishments, as individual sentences.

    Used by the audit so it sees everything Claude *said* it did -- not just the
    sentences that happened to match a probe.
    """
    if not transcript or not transcript.final_assistant_text:
        return []
    return _split_sentences(transcript.final_assistant_text)


def decompose_user_request(transcript: Transcript, model: str | None = None) -> list[str]:
    """Decompose the last user turn into the concrete things that were asked for.

    Uses the LLM judge (this is the deep path, so the extra call is acceptable).
    Returns [] when there is no user turn or the call fails -- never raises.
    """
    text = last_user_request(transcript)
    if not text:
        return []
    from .judge import decompose_request  # lazy: keeps subprocess imports off the fast path

    return decompose_request(text, model=model)

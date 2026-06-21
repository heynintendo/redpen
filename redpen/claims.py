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
    git = {"git_pushed", "git_clean"}
    names = ["git_pushed", "git_clean", "tests_pass", "todos_remaining", "contradiction_scan"]
    # git probes are optional here: in a non-git folder they're omitted rather
    # than run, so a generic "done" never produces git noise without a repo.
    return [ProbeSpec(n, label=PROBE_SUBJECT.get(n), optional=(n in git)) for n in names]


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
    # Skip non-claims ("nothing to report", "generated zero files", "didn't ...")
    # and descriptive path listings ("redpen/cli.py — the CLI") so neither
    # produces filler verdict lines. Only actual "I did X" assertions count.
    if patterns.is_non_claim(s) or patterns.is_listing(s):
        return []

    specs: list[ProbeSpec] = []

    # File paths: matched on the original sentence (the regex handles quoted
    # paths) and list-aware -- "Created X and Y" yields both X and Y.
    for path in patterns.created_paths(s):
        # These come from create/write/add verbs, so they are creation claims:
        # require the file to be in the session changed-set, not merely to exist.
        specs.append(ProbeSpec("file_present", {"path": path, "created": True}, label=f"wrote {path}"))

    # Action triggers run against a quote-stripped copy, so a verb the agent is
    # quoting or mocking ("'just push it'") is read as a mention, not a claim.
    sa = patterns.strip_quoted(s)

    if patterns.PUSH_RE.search(sa):
        specs.append(ProbeSpec("git_pushed"))
    if patterns.COMMIT_RE.search(sa):
        specs.append(ProbeSpec("git_clean"))
    if patterns.mentions_tests_passing(sa):
        specs.append(ProbeSpec("tests_pass"))
    if patterns.BUILD_RE.search(sa):
        specs.append(ProbeSpec("build_ok"))
    if patterns.LINT_RE.search(sa):
        specs.append(ProbeSpec("lint_clean"))
    if patterns.PR_RE.search(sa):
        specs.append(ProbeSpec("pr_status"))
    if patterns.BRANCH_SYNC_RE.search(sa):
        specs.append(ProbeSpec("branch_synced"))

    # probe-pack: dependency / type-check / test-count / symbol claims
    m = patterns.TEST_COUNT_RE.search(sa)
    if m:
        specs.append(ProbeSpec("test_count", {"n": int(m.group(1))}, label=f"all {m.group(1)} tests pass"))
    if patterns.TYPECHECK_RE.search(sa):
        specs.append(ProbeSpec("typecheck_clean"))
    dep = patterns.extract_dep(sa)
    if dep:
        specs.append(ProbeSpec("dep_present", {"name": dep}, label=f"added dependency {dep}"))
    sym = patterns.extract_symbol(sa)
    if sym:
        specs.append(ProbeSpec("symbol_exists", {"symbol": sym}, label=f"added {sym}"))

    # A generic "done/ready" claim pulls in the whole default suite.
    if patterns.DONE_RE.search(sa):
        for spec in default_suite():
            specs.append(spec)

    # Catch-all: an accomplishment claim with nothing a probe can check -- an
    # "I refactored X" or "all three run correctly" -- still gets surfaced as a
    # labelled UNVERIFIABLE line, never silently dropped.
    if not specs and len(s) <= 200 and (patterns.CLAIM_LIKE_RE.search(sa) or patterns.WORKS_RE.search(sa)):
        specs.append(ProbeSpec("unmapped", label=s[:80]))

    return _dedupe(specs)


def _dedupe(specs: list[ProbeSpec]) -> list[ProbeSpec]:
    kept: dict = {}
    order: list = []
    for spec in specs:
        k = spec.key()
        if k not in kept:
            kept[k] = spec
            order.append(k)
        elif kept[k].optional and not spec.optional:
            kept[k] = spec  # prefer the explicit (non-optional) spec
    return [kept[k] for k in order]


def _dedupe_across_claims(claims: list[Claim]) -> list[Claim]:
    """Drop a probe if an identical (name, kwargs) already ran for an earlier
    claim, so two "done"-ish sentences don't each expand the whole default suite.
    Prefer a non-optional (explicit) spec over an optional one for the same key.
    """
    winner: dict = {}
    for claim in claims:
        for s in claim.probe_specs:
            k = s.key()
            if k not in winner or (winner[k].optional and not s.optional):
                winner[k] = s
    emitted: set = set()
    out: list[Claim] = []
    for claim in claims:
        kept = []
        for s in claim.probe_specs:
            k = s.key()
            if k in emitted:
                continue
            if s is winner[k]:
                kept.append(s)
                emitted.add(k)
        if kept:
            claim.probe_specs = kept
            out.append(claim)
    return out


_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)


def _strip_code_fences(text: str) -> str:
    """Drop fenced code blocks: trigger words/commands inside ``` ``` are example
    code, not claims (a `# tests pass` comment or a `git push` recipe)."""
    return _CODE_FENCE_RE.sub(" ", text)


def extract_claims(text: str, source: str = "transcript") -> list[Claim]:
    """Map a block of text to a list of (claim, probes)."""
    text = _strip_code_fences(text)
    claims: list[Claim] = []
    for sentence in _split_sentences(text):
        specs = _specs_for_sentence(sentence)
        if specs:
            claims.append(Claim(text=sentence, probe_specs=specs, source=source))

    if claims:
        return _dedupe_across_claims(claims)

    # Nothing specific matched. Text that clearly narrates completion runs the
    # full default suite -- UNLESS it's negating completion ("not done yet, still
    # uncommitted, haven't pushed"), which is not a claim and grades nothing. An
    # ad-hoc question we can't map becomes an honest UNVERIFIABLE line.
    label = (text.strip() or "completion")[:120]
    narrates = patterns.DONE_RE.search(text) or patterns.narrates_success(text)
    if narrates and not patterns.is_non_claim(text):
        return [Claim(text=label, probe_specs=default_suite(), source=source)]
    if source == "adhoc" and not patterns.is_non_claim(text):
        return [Claim(text=label, probe_specs=[ProbeSpec("unmapped", label=label[:80])], source=source)]
    return []


# Probes that only make sense inside a git repository.
GIT_PROBES = {"git_pushed", "git_clean", "branch_synced", "pr_status"}


def drop_inapplicable_git_probes(claims: list[Claim], is_git: bool) -> list[Claim]:
    """In a non-git folder, omit the generic-suite git probes entirely (they'd be
    noise), keeping only *explicit* git claims (which then FAIL as contradictions
    -- you can't push/commit in a non-repo). No-op inside a git repository.
    """
    if is_git:
        return claims
    out: list[Claim] = []
    for claim in claims:
        kept = [s for s in claim.probe_specs if not (s.name in GIT_PROBES and s.optional)]
        if kept:
            claim.probe_specs = kept
            out.append(claim)
    return out


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

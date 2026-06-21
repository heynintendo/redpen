"""Regex vocabulary shared between the claim extractor and the probes.

Kept in one place so the language RedPen treats as a "success claim" is
defined once. Precision matters more than recall everywhere here.
"""

from __future__ import annotations

import re

# Generic completion / success narration. Used by exit_code_scan to decide
# whether the assistant *narrated* success, and by the claim extractor as the
# trigger for the full default suite ("done", "ready", ...).
SUCCESS_RE = re.compile(
    r"\b("
    r"done|complete[ds]?|finished|ready|all set|good to go|"
    r"works|working|succeed(?:s|ed)?|success(?:ful|fully)?|"
    r"pass(?:es|ed|ing)?|green|"
    r"pushed|deployed|merged"
    r")\b|✅|✓",
    re.IGNORECASE,
)


def narrates_success(text: str) -> bool:
    """True if the text reads as a claim that something succeeded/completed."""
    return bool(text and SUCCESS_RE.search(text))


# --- claim -> probe trigger patterns ----------------------------------------
# Each is matched against a single sentence/line of the assistant's message.

PUSH_RE = re.compile(r"\bpush(?:ed|ing|es)?\b", re.IGNORECASE)
COMMIT_RE = re.compile(r"\bcommit(?:ted|ting|s)?\b", re.IGNORECASE)
PR_RE = re.compile(r"\b(pull request|PR)\b|\bgh pr\b", re.IGNORECASE)
BRANCH_SYNC_RE = re.compile(
    r"\bbranch\b[^.]*\b(synced?|in sync|up[- ]to[- ]date|matches?)\b"
    r"|\b(synced?|up[- ]to[- ]date)\b[^.]*\bremote\b",
    re.IGNORECASE,
)
BUILD_RE = re.compile(
    r"\bbuild(?:s|ed|ing)?\b[^.]*\b(pass\w*|succeed\w*|works?|clean|green|ok)\b"
    r"|\b(compiles?|compiled)\b",
    re.IGNORECASE,
)
LINT_RE = re.compile(
    r"\blint(?:er|ed|ing|s)?\b[^.]*\b(clean|pass\w*|no (?:issues|errors|warnings)|green)\b"
    r"|\bno lint\b",
    re.IGNORECASE,
)

# Tests: require both a "test" token and a "pass/green" token to avoid firing on
# any mention of the word "test".
_TEST_TOKEN = re.compile(r"\btests?\b|\btest suite\b|\bpytest\b|\bunit tests?\b", re.IGNORECASE)
_PASS_TOKEN = re.compile(r"\bpass(?:es|ed|ing)?\b|\bgreen\b|\ball pass\b|\bsucceed\w*\b", re.IGNORECASE)


def mentions_tests_passing(sentence: str) -> bool:
    return bool(_TEST_TOKEN.search(sentence) and _PASS_TOKEN.search(sentence))


# File creation: a create/write verb followed by a path-looking token (has a
# dot-extension or a slash). Tight on purpose so "version 0.1" never matches.
FILE_RE = re.compile(
    r"\b(?:creat(?:e|ed)|wr(?:ote|itten)|add(?:ed)?|generat(?:e|ed)|"
    r"sav(?:e|ed)|updat(?:e|ed)|modif(?:y|ied)|edit(?:ed)?|chang(?:e|ed)|"
    r"implement(?:ed)?)\b"
    r"\s+(?:the\s+|a\s+|an\s+|new\s+)?(?:file\s+|module\s+|script\s+)?"
    r"[`'\"]?(?P<path>[\w./\-]+(?:/[\w.\-]+|\.[A-Za-z0-9]+))[`'\"]?",
    re.IGNORECASE,
)

# A continuation path in a conjunction: "Created X and Y" / "X, Y, and Z". The
# verb only governs the first path, so further paths joined by and/comma/& would
# otherwise be missed.
_FILE_PATH = r"[\w./\-]+(?:/[\w.\-]+|\.[A-Za-z0-9]+)"
_CONT_PATH_RE = re.compile(
    r"\s*(?:,|,?\s*and|&)\s+[`'\"]?(?P<path>" + _FILE_PATH + r")[`'\"]?",
    re.IGNORECASE,
)


def created_paths(sentence: str) -> list[str]:
    """All paths in a creation claim, including ones after 'and'/',' in a list.

    "Created src/x.py and src/y.py" -> ['src/x.py', 'src/y.py'].
    """
    out: list[str] = []
    seen: set[str] = set()

    def _add(p: str) -> None:
        if p not in seen:
            seen.add(p)
            out.append(p)

    for m in FILE_RE.finditer(sentence):
        _add(m.group("path"))
        pos = m.end()
        while True:  # consume "and PATH" / ", PATH" continuations
            cm = _CONT_PATH_RE.match(sentence, pos)
            if not cm:
                break
            _add(cm.group("path"))
            pos = cm.end()
    return out


# Quoting / mockery: text the agent puts in quotes (or backticks) is a *mention*,
# not an assertion ("Yeah, because 'just push it' is ever that simple"). Strip
# quoted spans before matching action triggers so a quoted verb isn't read as a
# claim. The single-quote rule skips contraction apostrophes (haven't, it's) by
# requiring the quote not be glued to letters on either side.
_QUOTED_RE = re.compile(
    r"\"[^\"]*\""
    r"|`[^`]*`"
    r"|(?<![A-Za-z])'[^']*'(?![A-Za-z])"
)


def strip_quoted(s: str) -> str:
    """Blank out quoted spans (mentions/mockery) for action-trigger matching."""
    return _QUOTED_RE.sub(" ", s)


# Non-claims: a sentence asserting that NOTHING was done (or describing session
# state) is not a completion claim and must not produce a verdict line. Kept
# specific so it never swallows a real positive like "no type errors".
NON_CLAIM_RE = re.compile(
    r"\bnothing\s+(?:to|was|else|much|changed|left)\b"
    r"|\bno\s+(?:files?|changes?|commits?|edits?|work|action)\b"
    r"|\b(?:zero|no)\s+files?\b|\bgenerated\s+(?:zero|no|0)\b|\b0\s+files?\b"
    r"|\bfresh\s+session\b|\bnew\s+session\b|\bempty\s+session\b|\bno-?op\b"
    r"|\bdid\s*n[o']?t\b|\bdo\s*n[o']?t\b|\bdoes\s*n[o']?t\b|\bwas\s*n[o']?t\b"
    r"|\bwere\s*n[o']?t\b|\bhave\s*n[o']?t\b|\bhas\s*n[o']?t\b"
    r"|\bnot\s+(?:yet\s+)?(?:created|added|written|implemented|done|made|pushed|committed)\b",
    re.IGNORECASE,
)


def is_non_claim(sentence: str) -> bool:
    """True if the sentence asserts nothing-was-done / session state, not a claim."""
    return bool(NON_CLAIM_RE.search(sentence))


# Descriptive enumeration -- a bare path, or "path/ — description" / "path: desc"
# (directory listings, the agent narrating what files exist). These are NOT
# completion claims. A real claim starts with a verb ("Created config.py"), so a
# line that STARTS with a path token is a listing, not an assertion.
_PATH_TOKEN = r"[`'\"]?(?:[\w@.\-]+(?:[/.][\w@.\-]+)+/?|[\w@.\-]+/)[`'\"]?"
_LISTING_RE = re.compile(
    rf"^\s*{_PATH_TOKEN}\s*$"                      # a path on its own line
    rf"|^\s*{_PATH_TOKEN}\s*(?:[—–:]|-)\s+\S",     # "path — desc" / "path: desc" / "path - desc"
    re.IGNORECASE,
)


def is_listing(sentence: str) -> bool:
    """True if the line is a path listing / 'path — description', not a claim."""
    return bool(_LISTING_RE.match(sentence))

# An assertive accomplishment sentence ("I refactored the parser") even when it
# names nothing a probe can check. Used by the catch-all so such a claim becomes
# a labelled UNVERIFIABLE line instead of being silently dropped.
CLAIM_LIKE_RE = re.compile(
    r"\b(?:created|added|wrote|implemented|refactored|fixed|resolved|removed|deleted|"
    r"renamed|moved|configured|installed|set up|setup|updated|changed|built|generated|"
    r"enabled|disabled|migrated|integrated|wired|replaced|introduced|cleaned up|"
    r"rewrote|extracted|optimi[sz]ed|documented)\b",
    re.IGNORECASE,
)


# "all N tests pass" -> verify the exact count from the transcript.
TEST_COUNT_RE = re.compile(
    r"\b(?:all\s+)?(\d+)\s+(?:unit\s+|integration\s+)?tests?\s+"
    r"(?:pass|passing|passed|are\s+passing|green)\b",
    re.IGNORECASE,
)

# A configured type checker is clean.
TYPECHECK_RE = re.compile(
    r"\b(?:mypy|pyright|tsc)\b[^.]*\b(?:pass\w*|clean|green|no (?:type )?errors|0 errors)\b"
    r"|\bno type errors\b|\btype[- ]?check\w*\b[^.]*\b(?:pass\w*|clean|green)\b",
    re.IGNORECASE,
)

_DEP_KEYWORD = re.compile(
    r"\b(?:added|installed|bumped|upgraded|pinned)\s+(?:the\s+|a\s+|new\s+)?"
    r"(?:dependency|dep|package|library|crate|module)\s+[`'\"]?([\w.@/-]+)[`'\"]?",
    re.IGNORECASE,
)
_DEP_TO_MANIFEST = re.compile(
    r"\b(?:added|installed|pinned)\s+[`'\"]?([\w.@/-]+)[`'\"]?\s+to\s+"
    r"(?:package\.json|requirements(?:\.txt)?|Cargo\.toml|pyproject(?:\.toml)?|the dependencies|deps)",
    re.IGNORECASE,
)
_SYMBOL_KEYWORD = re.compile(
    r"\b(?:added|created|implemented|wrote|defined|introduced)\s+(?:a\s+|an\s+|the\s+|new\s+)?"
    r"(?:function|method|class|endpoint|route|component|handler|hook|fn|func|api)\s+"
    r"[`'\"]?([A-Za-z_/][\w/.\-]*)[`'\"]?",
    re.IGNORECASE,
)


def extract_dep(sentence: str) -> str | None:
    for pat in (_DEP_KEYWORD, _DEP_TO_MANIFEST):
        m = pat.search(sentence)
        if m:
            return m.group(1)
    return None


def extract_symbol(sentence: str) -> str | None:
    m = _SYMBOL_KEYWORD.search(sentence)
    return m.group(1) if m else None


# Generic "done / complete / ready / finished" -> run the default suite.
DONE_RE = re.compile(
    r"\b(done|complete[ds]?|finished|ready|all set|good to go|"
    r"everything works|that's it|ship it)\b|✅",
    re.IGNORECASE,
)

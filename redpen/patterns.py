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
    r"sav(?:e|ed)|updat(?:e|ed)|implement(?:ed)?)\b"
    r"\s+(?:the\s+|a\s+|an\s+|new\s+)?(?:file\s+|module\s+|script\s+)?"
    r"[`'\"]?(?P<path>[\w./\-]+(?:/[\w.\-]+|\.[A-Za-z0-9]+))[`'\"]?",
    re.IGNORECASE,
)

# Generic "done / complete / ready / finished" -> run the default suite.
DONE_RE = re.compile(
    r"\b(done|complete[ds]?|finished|ready|all set|good to go|"
    r"everything works|that's it|ship it)\b|✅",
    re.IGNORECASE,
)

"""Shared builders for the ultra case modules."""

from __future__ import annotations

from uharness.builders import TB, make_repo
from uharness.model import OK, UNV, Built, Case, ef


def suite_efs(overrides=None):
    """The five default-suite EFs for a clean, no-upstream, no-touched-file repo."""
    base = {
        "git_pushed": ef("git_pushed", true=None),
        "git_clean": ef("git_clean", true=True),
        "tests_pass": ef("tests_pass", true=None),
        "todos_remaining": ef("todos_remaining", true=None, accept={UNV}),
        "contradiction_scan": ef("contradiction_scan", true=True, accept={OK}),
    }
    base.update(overrides or {})
    return list(base.values())


def case(cid, axis, title, build, efs, *, tags=(), allow_phantom=(), deep=False, invariant=None):
    return Case(cid, axis, title, build, list(efs), tags=tuple(tags),
                allow_phantom=frozenset(allow_phantom), deep=deep, invariant=invariant)


def repo_tx(ws, *, final, bash=None, writes=None, user="finish the work",
            files=None, commit=True, pre=None):
    """Materialize a git repo + transcript and return a Built."""
    root = make_repo(ws / "repo", files or {"README.md": "# x\n"}, commit=commit)
    t = TB(cwd=root)
    if user:
        t.user(user)
    for w in (writes or []):
        t.write(w)
    for ev in (bash or []):
        t.bash(ev["cmd"], output=ev.get("out", ""), failed=ev.get("failed", False),
               exit_code=ev.get("exit_code"))
    for msg in (pre or []):
        t.assistant(msg)
    t.assistant(final)
    return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))

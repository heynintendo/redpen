"""Shared builders for terse case definitions.

A "tool event" is a dict {cmd, out, failed, exit_code}. Writes are file paths the
agent's transcript records as Write tool-uses (so they enter the changed-set).
Order in the transcript: user prompt -> writes -> bash events -> any
pre_assistant texts -> the final assistant message (the claim source).
"""

from __future__ import annotations

from harness.builders import TB, commit_all, git, make_repo, write_file
from harness.model import OK, UNV, Built, Case, ef


def suite_efs(overrides=None):
    """The five default-suite EFs (clean, no-upstream, no-touched-file repo).

    Pass ``overrides`` as {probe: EF} to tighten/relax individual findings, e.g.
    ``suite_efs({"contradiction_scan": ef("contradiction_scan", true=True, accept={OK, UNV})})``.
    """
    base = {
        "git_pushed": ef("git_pushed", true=None),
        "git_clean": ef("git_clean", true=True),
        "tests_pass": ef("tests_pass", true=None),
        "todos_remaining": ef("todos_remaining", true=None, accept={UNV}),
        "contradiction_scan": ef("contradiction_scan", true=True, accept={OK}),
    }
    base.update(overrides or {})
    return list(base.values())


def build_repo_transcript(
    ws,
    *,
    final,
    files=None,
    user="please finish the work",
    writes=None,
    bash=None,
    pre_assistant=None,
    repo=True,
    commit=True,
    gitignore=True,
    session_id="sess-stress",
    transcript_kwargs=None,
):
    """Materialize a repo + transcript and return a Built. Reused by most cases."""
    root = ws / "repo"
    if repo:
        make_repo(root, files or {"README.md": "# project\n"}, commit=commit, gitignore_redpen=gitignore)
    else:
        root.mkdir(parents=True, exist_ok=True)
        for rel, content in (files or {}).items():
            write_file(root, rel, content)
    t = TB(cwd=root, session_id=session_id)
    if user:
        t.user(user)
    for rel in (writes or []):
        t.write(rel)
    for ev in (bash or []):
        t.bash(ev["cmd"], output=ev.get("out", ""), failed=ev.get("failed", False),
               exit_code=ev.get("exit_code"))
    for msg in (pre_assistant or []):
        t.assistant(msg)
    t.assistant(final)
    tp = t.write_to(ws / "t.jsonl", **(transcript_kwargs or {}))
    return Built(cwd=root, transcript=tp)


def basic_case(
    cid,
    axis,
    title,
    *,
    final,
    efs,
    files=None,
    user="please finish the work",
    writes=None,
    bash=None,
    pre_assistant=None,
    allow_phantom=(),
    repo=True,
    commit=True,
    gitignore=True,
    invariant=None,
    deep=False,
):
    def build(ws, rng, _final=final, _files=files, _user=user, _writes=writes,
              _bash=bash, _pre=pre_assistant, _repo=repo, _commit=commit, _gi=gitignore):
        return build_repo_transcript(
            ws, final=_final, files=_files, user=_user, writes=_writes, bash=_bash,
            pre_assistant=_pre, repo=_repo, commit=_commit, gitignore=_gi,
        )

    return Case(cid, axis, title, build, list(efs), allow_phantom=frozenset(allow_phantom),
                invariant=invariant, deep=deep)

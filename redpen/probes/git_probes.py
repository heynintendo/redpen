"""Git probes: git_pushed, git_clean, branch_synced.

git_pushed and git_clean are purely local (no network). branch_synced makes
one explicit network call via ``git ls-remote`` -- it only runs when a claim
specifically asserts the branch is synced with the remote.
"""

from __future__ import annotations

from pathlib import Path

from ..util import run
from .base import ProbeContext, ProbeResult, fail, ok, unverifiable


def _is_git_repo(ctx: ProbeContext) -> bool:
    rc, out, _ = run(["git", "rev-parse", "--is-inside-work-tree"], cwd=ctx.cwd)
    return rc == 0 and out.strip() == "true"


def _in_progress_op(ctx: ProbeContext) -> str | None:
    """Name of an in-flight git operation (rebase/merge/...), or None."""
    rc, gd, _ = run(["git", "rev-parse", "--absolute-git-dir"], cwd=ctx.cwd)
    if rc != 0:
        return None
    g = Path(gd.strip())
    if (g / "rebase-merge").exists() or (g / "rebase-apply").exists():
        return "rebase"
    if (g / "MERGE_HEAD").exists():
        return "merge"
    if (g / "CHERRY_PICK_HEAD").exists():
        return "cherry-pick"
    if (g / "REVERT_HEAD").exists():
        return "revert"
    return None


def git_pushed(ctx: ProbeContext, **_: object) -> ProbeResult:
    """Verify a "pushed to remote" claim via the upstream tracking branch.

    Uses ``git rev-list --count @{u}..HEAD`` -- local, no network. Every state
    where we can't actually compare (no upstream, no remote, detached HEAD, no
    commits, mid-rebase/merge) is UNVERIFIABLE, never FAIL.
    """
    if not _is_git_repo(ctx):
        # Reached only for an explicit "pushed" claim (the generic-suite git
        # probes are dropped in a non-git folder).
        return fail("git_pushed", "you claimed a push, but this folder is not a git repository", git=False)

    op = _in_progress_op(ctx)
    if op:
        return unverifiable("git_pushed", f"a {op} is in progress, so the repo is mid-change — I can't tell if it's pushed", operation=op)

    # Unborn branch / zero commits -> nothing to have pushed.
    rc, _, _ = run(["git", "rev-parse", "--verify", "-q", "HEAD"], cwd=ctx.cwd)
    if rc != 0:
        return unverifiable("git_pushed", "there are no commits yet, so nothing could have been pushed")

    # Detached HEAD -> not on a branch, so no upstream to compare.
    rc_sym, _, _ = run(["git", "symbolic-ref", "-q", "HEAD"], cwd=ctx.cwd)
    if rc_sym != 0:
        return unverifiable("git_pushed", "HEAD is detached (not on a branch), so there's no upstream to compare against")

    rc, upstream, _ = run(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=ctx.cwd
    )
    if rc != 0:
        _, remotes, _ = run(["git", "remote"], cwd=ctx.cwd)
        if not remotes.strip():
            return unverifiable("git_pushed", "no remote is set up, so there's nowhere to push", upstream=None)
        return unverifiable(
            "git_pushed",
            "this branch has no upstream set, so I can't tell whether it's been pushed",
            upstream=None,
        )
    upstream = upstream.strip()

    rc, count, _ = run(["git", "rev-list", "--count", "@{u}..HEAD"], cwd=ctx.cwd)
    if rc != 0:
        return unverifiable("git_pushed", "couldn't work out how many commits are unpushed", upstream=upstream)

    ahead = int(count.strip() or "0")
    if ahead == 0:
        return ok("git_pushed", f"nothing left to push — you're level with {upstream}", ahead=0, upstream=upstream)

    _, log, _ = run(["git", "log", "--oneline", "-n", "10", "@{u}..HEAD"], cwd=ctx.cwd)
    commits = [ln for ln in log.splitlines() if ln.strip()]
    plural = "commit hasn't" if ahead == 1 else "commits haven't"
    return fail(
        "git_pushed",
        f"{ahead} {plural} been pushed yet",
        ahead=ahead,
        upstream=upstream,
        commits=commits,
    )


def git_clean(ctx: ProbeContext, **_: object) -> ProbeResult:
    """Verify a "committed everything / working tree clean" claim.

    ``git status --porcelain`` excludes ignored files by default and reports
    staged / modified / untracked distinctly. Any of them contradicts a
    "clean / committed everything" claim, so FAIL with the breakdown.
    """
    if not _is_git_repo(ctx):
        return fail("git_clean", "you claimed a commit, but this folder is not a git repository", git=False)

    # A rebase/merge/cherry-pick/revert in progress is a transient state: the
    # tree is mid-operation (unmerged paths are expected), so "committed
    # everything / clean" can't be judged -> UNVERIFIABLE, never FAIL. Mirrors
    # git_pushed, which already bails on in-progress operations.
    op = _in_progress_op(ctx)
    if op:
        return unverifiable("git_clean", f"a {op} is in progress, so the tree is mid-change — I can't tell if it's clean", operation=op)

    rc, out, _ = run(["git", "status", "--porcelain"], cwd=ctx.cwd)
    if rc != 0:
        return unverifiable("git_clean", "couldn't read git status")

    lines = [ln for ln in out.splitlines() if ln.strip()]
    if not lines:
        return ok("git_clean", "nothing uncommitted — the working tree is clean", staged=0, modified=0, untracked=0)

    staged = modified = untracked = 0
    files: list[str] = []
    agent_files: list[str] = []
    cs = ctx.changed_set
    for ln in lines:
        x, y = ln[0], ln[1]
        rel = ln[3:]
        files.append(rel)
        # A rename shows as "old -> new"; attribute on the new path.
        attr_path = rel.split(" -> ")[-1] if " -> " in rel else rel
        is_untracked = x == "?" and y == "?"
        is_staged = not is_untracked and x not in (" ", "?")
        has_transcript = cs is not None and "transcript" in cs.provenance(ctx.cwd, attr_path)
        # A dirty file is the session's own uncommitted work -- and so a real
        # contradiction of "committed everything" -- when it's a NEW untracked
        # file, a STAGED change, or a tracked file THIS session's transcript
        # edited. A tracked file that was only modified in the worktree with no
        # transcript record is the one TOCTOU shape: it may be a user's edit
        # after the agent finished, so it isn't attributable to the agent.
        if is_untracked or is_staged or has_transcript:
            agent_files.append(attr_path)
        if is_untracked:
            untracked += 1
        else:
            if x not in (" ", "?"):
                staged += 1
            if y not in (" ", "?"):
                modified += 1

    # Attribution / TOCTOU guard: when we have a changed-set and every dirty file
    # is a worktree-only edit to a tracked file the agent never touched, the
    # dirtiness isn't the agent's -- it's a post-finish user edit or another
    # session -- so it's UNVERIFIABLE, not FAIL.
    if cs is not None and not agent_files:
        return unverifiable(
            "git_clean",
            f"the tree has {len(lines)} uncommitted change(s), but none were made by this "
            "session — looks like edits after the agent finished, not the agent's to answer for",
            staged=staged,
            modified=modified,
            untracked=untracked,
            files=files[:20],
            agent_attributable=False,
        )

    parts = []
    if staged:
        parts.append(f"{staged} staged")
    if modified:
        parts.append(f"{modified} modified")
    if untracked:
        parts.append(f"{untracked} untracked")
    return fail(
        "git_clean",
        f"{len(lines)} change(s) aren't committed yet: {', '.join(parts)}",
        staged=staged,
        modified=modified,
        untracked=untracked,
        files=files[:20],
        agent_files=agent_files[:20],
    )


def branch_synced(ctx: ProbeContext, **_: object) -> ProbeResult:
    """Verify local HEAD matches the remote branch tip (explicit network probe).

    Makes one ``git ls-remote`` call. If the remote is unreachable we return
    UNVERIFIABLE rather than FAIL -- not being able to reach the network does
    not contradict the claim.
    """
    if not _is_git_repo(ctx):
        return fail("branch_synced", "you claimed the branch is synced, but this folder is not a git repository", git=False)

    rc, upstream, _ = run(["git", "rev-parse", "--abbrev-ref", "@{u}"], cwd=ctx.cwd)
    if rc != 0:
        return unverifiable("branch_synced", "this branch has no upstream, so there's nothing to sync against")
    upstream = upstream.strip()
    if "/" not in upstream:
        return unverifiable("branch_synced", f"couldn't make sense of the upstream ref ({upstream})")
    remote, rbranch = upstream.split("/", 1)

    rc, local, _ = run(["git", "rev-parse", "HEAD"], cwd=ctx.cwd)
    if rc != 0:
        return unverifiable("branch_synced", "couldn't resolve the local HEAD commit")
    local_sha = local.strip()

    rc, out, err = run(["git", "ls-remote", remote, rbranch], cwd=ctx.cwd)
    if rc != 0:
        return unverifiable(
            "branch_synced",
            "couldn't reach the remote to compare",
            remote=remote,
            error=err.strip()[:200],
        )
    if not out.strip():
        return unverifiable("branch_synced", f"the remote has no {rbranch} branch to compare against", remote=remote)

    remote_sha = out.split()[0]
    if remote_sha == local_sha:
        return ok("branch_synced", f"in sync — local matches {upstream}", local=local_sha[:12], remote=remote_sha[:12])
    return fail(
        "branch_synced",
        f"local and {upstream} point at different commits",
        local=local_sha[:12],
        remote=remote_sha[:12],
    )

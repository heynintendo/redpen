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
        # probes are dropped in a non-git folder). You can't push from a folder
        # that isn't a repo -> the claim is contradicted.
        return fail("git_pushed", "claimed a push, but this is not a git repository", git=False)

    op = _in_progress_op(ctx)
    if op:
        return unverifiable("git_pushed", f"{op} in progress; repository state is mid-flight", operation=op)

    # Unborn branch / zero commits -> nothing to have pushed.
    rc, _, _ = run(["git", "rev-parse", "--verify", "-q", "HEAD"], cwd=ctx.cwd)
    if rc != 0:
        return unverifiable("git_pushed", "no commits yet (unborn branch)")

    # Detached HEAD -> not on a branch, so no upstream to compare.
    rc_sym, _, _ = run(["git", "symbolic-ref", "-q", "HEAD"], cwd=ctx.cwd)
    if rc_sym != 0:
        return unverifiable("git_pushed", "detached HEAD; not on a branch to compare")

    rc, upstream, _ = run(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=ctx.cwd
    )
    if rc != 0:
        _, remotes, _ = run(["git", "remote"], cwd=ctx.cwd)
        if not remotes.strip():
            return unverifiable("git_pushed", "no remote configured (nothing to push to)", upstream=None)
        return unverifiable(
            "git_pushed",
            "no upstream branch configured (nothing to compare against)",
            upstream=None,
        )
    upstream = upstream.strip()

    rc, count, _ = run(["git", "rev-list", "--count", "@{u}..HEAD"], cwd=ctx.cwd)
    if rc != 0:
        return unverifiable("git_pushed", "could not compute unpushed commits", upstream=upstream)

    ahead = int(count.strip() or "0")
    if ahead == 0:
        return ok("git_pushed", f"no unpushed commits (level with {upstream})", ahead=0, upstream=upstream)

    _, log, _ = run(["git", "log", "--oneline", "-n", "10", "@{u}..HEAD"], cwd=ctx.cwd)
    commits = [ln for ln in log.splitlines() if ln.strip()]
    return fail(
        "git_pushed",
        f"{ahead} unpushed commit(s) on this branch",
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
        return fail("git_clean", "claimed a commit, but this is not a git repository", git=False)

    rc, out, _ = run(["git", "status", "--porcelain"], cwd=ctx.cwd)
    if rc != 0:
        return unverifiable("git_clean", "could not read git status")

    lines = [ln for ln in out.splitlines() if ln.strip()]
    if not lines:
        return ok("git_clean", "working tree is clean", staged=0, modified=0, untracked=0)

    staged = modified = untracked = 0
    files: list[str] = []
    for ln in lines:
        x, y = ln[0], ln[1]
        files.append(ln[3:])
        if x == "?" and y == "?":
            untracked += 1
        else:
            if x not in (" ", "?"):
                staged += 1
            if y not in (" ", "?"):
                modified += 1

    parts = []
    if staged:
        parts.append(f"{staged} staged")
    if modified:
        parts.append(f"{modified} modified")
    if untracked:
        parts.append(f"{untracked} untracked")
    return fail(
        "git_clean",
        f"{len(lines)} uncommitted change(s): {', '.join(parts)}",
        staged=staged,
        modified=modified,
        untracked=untracked,
        files=files[:20],
    )


def branch_synced(ctx: ProbeContext, **_: object) -> ProbeResult:
    """Verify local HEAD matches the remote branch tip (explicit network probe).

    Makes one ``git ls-remote`` call. If the remote is unreachable we return
    UNVERIFIABLE rather than FAIL -- not being able to reach the network does
    not contradict the claim.
    """
    if not _is_git_repo(ctx):
        return fail("branch_synced", "claimed branch sync, but this is not a git repository", git=False)

    rc, upstream, _ = run(["git", "rev-parse", "--abbrev-ref", "@{u}"], cwd=ctx.cwd)
    if rc != 0:
        return unverifiable("branch_synced", "no upstream tracking branch configured")
    upstream = upstream.strip()
    if "/" not in upstream:
        return unverifiable("branch_synced", f"cannot parse upstream ref: {upstream}")
    remote, rbranch = upstream.split("/", 1)

    rc, local, _ = run(["git", "rev-parse", "HEAD"], cwd=ctx.cwd)
    if rc != 0:
        return unverifiable("branch_synced", "could not resolve local HEAD")
    local_sha = local.strip()

    rc, out, err = run(["git", "ls-remote", remote, rbranch], cwd=ctx.cwd)
    if rc != 0:
        return unverifiable(
            "branch_synced",
            "could not reach remote to compare",
            remote=remote,
            error=err.strip()[:200],
        )
    if not out.strip():
        return unverifiable("branch_synced", f"remote has no branch {rbranch}", remote=remote)

    remote_sha = out.split()[0]
    if remote_sha == local_sha:
        return ok("branch_synced", f"local matches {upstream}", local=local_sha[:12], remote=remote_sha[:12])
    return fail(
        "branch_synced",
        f"local HEAD differs from {upstream}",
        local=local_sha[:12],
        remote=remote_sha[:12],
    )

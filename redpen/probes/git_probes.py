"""Git probes: git_pushed, git_clean, branch_synced.

git_pushed and git_clean are purely local (no network). branch_synced makes
one explicit network call via ``git ls-remote`` -- it only runs when a claim
specifically asserts the branch is synced with the remote.
"""

from __future__ import annotations

from ..util import run
from .base import ProbeContext, ProbeResult, fail, ok, unverifiable


def _is_git_repo(ctx: ProbeContext) -> bool:
    rc, out, _ = run(["git", "rev-parse", "--is-inside-work-tree"], cwd=ctx.cwd)
    return rc == 0 and out.strip() == "true"


def git_pushed(ctx: ProbeContext, **_: object) -> ProbeResult:
    """Verify a "pushed to remote" claim via the upstream tracking branch.

    Uses ``git rev-list --count @{u}..HEAD`` -- local, no network. With no
    upstream we genuinely cannot tell, so UNVERIFIABLE (not FAIL).
    """
    if not _is_git_repo(ctx):
        return unverifiable("git_pushed", "not a git repository")

    rc, upstream, _ = run(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=ctx.cwd
    )
    if rc != 0:
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
    """Verify a "committed everything / working tree clean" claim."""
    if not _is_git_repo(ctx):
        return unverifiable("git_clean", "not a git repository")

    rc, out, _ = run(["git", "status", "--porcelain"], cwd=ctx.cwd)
    if rc != 0:
        return unverifiable("git_clean", "could not read git status")

    changes = [ln for ln in out.splitlines() if ln.strip()]
    if not changes:
        return ok("git_clean", "working tree is clean", changes=0)
    return fail(
        "git_clean",
        f"{len(changes)} uncommitted change(s) in the working tree",
        changes=len(changes),
        files=[ln[3:] for ln in changes[:20]],
    )


def branch_synced(ctx: ProbeContext, **_: object) -> ProbeResult:
    """Verify local HEAD matches the remote branch tip (explicit network probe).

    Makes one ``git ls-remote`` call. If the remote is unreachable we return
    UNVERIFIABLE rather than FAIL -- not being able to reach the network does
    not contradict the claim.
    """
    if not _is_git_repo(ctx):
        return unverifiable("branch_synced", "not a git repository")

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

"""GitHub probe: pr_status (explicit network probe via the gh CLI)."""

from __future__ import annotations

import json
import shutil

from ..util import run
from .base import ProbeContext, ProbeResult, fail, ok, unverifiable


def pr_status(ctx: ProbeContext, **_: object) -> ProbeResult:
    """Verify a "opened/created a PR" claim via the gh CLI.

    If gh is missing or unauthenticated we return UNVERIFIABLE with a one-line
    hint -- absence of the tool is not evidence the PR doesn't exist. A PR that
    exists for the current branch is OK and we report its state.
    """
    rc, out, _ = run(["git", "rev-parse", "--is-inside-work-tree"], cwd=ctx.cwd)
    if not (rc == 0 and out.strip() == "true"):
        # A PR requires a git repository -> claiming one here is a contradiction.
        return fail("pr_status", "you claimed a PR, but this folder is not a git repository", git=False)

    if shutil.which("gh") is None:
        return unverifiable(
            "pr_status",
            "the gh CLI isn't installed, so I can't check for a PR (install GitHub CLI)",
            hint="https://cli.github.com",
        )

    rc, _, _ = run(["gh", "auth", "status"], cwd=ctx.cwd)
    if rc != 0:
        return unverifiable("pr_status", "gh isn't logged in, so I can't check for a PR — run `gh auth login`")

    rc, out, err = run(
        ["gh", "pr", "view", "--json", "state,number,title,url"], cwd=ctx.cwd
    )
    if rc != 0:
        return unverifiable(
            "pr_status",
            "I couldn't find a pull request for this branch",
            detail_err=err.strip()[:160],
        )

    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return unverifiable("pr_status", "couldn't read gh's response")

    state = data.get("state", "UNKNOWN")
    number = data.get("number")
    return ok(
        "pr_status",
        f"found PR #{number} ({state.lower()})",
        number=number,
        state=state,
        url=data.get("url"),
        title=data.get("title"),
    )

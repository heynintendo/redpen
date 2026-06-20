"""GitHub probe: pr_status (explicit network probe via the gh CLI)."""

from __future__ import annotations

import json
import shutil

from ..util import run
from .base import ProbeContext, ProbeResult, ok, unverifiable


def pr_status(ctx: ProbeContext, **_: object) -> ProbeResult:
    """Verify a "opened/created a PR" claim via the gh CLI.

    If gh is missing or unauthenticated we return UNVERIFIABLE with a one-line
    hint -- absence of the tool is not evidence the PR doesn't exist. A PR that
    exists for the current branch is OK and we report its state.
    """
    if shutil.which("gh") is None:
        return unverifiable(
            "pr_status",
            "gh CLI not installed -- install GitHub CLI to verify PR claims",
            hint="https://cli.github.com",
        )

    rc, _, _ = run(["gh", "auth", "status"], cwd=ctx.cwd)
    if rc != 0:
        return unverifiable("pr_status", "gh is not authenticated -- run `gh auth login`")

    rc, out, err = run(
        ["gh", "pr", "view", "--json", "state,number,title,url"], cwd=ctx.cwd
    )
    if rc != 0:
        return unverifiable(
            "pr_status",
            "no pull request found for the current branch",
            detail_err=err.strip()[:160],
        )

    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return unverifiable("pr_status", "could not parse gh output")

    state = data.get("state", "UNKNOWN")
    number = data.get("number")
    return ok(
        "pr_status",
        f"PR #{number} exists ({state})",
        number=number,
        state=state,
        url=data.get("url"),
        title=data.get("title"),
    )

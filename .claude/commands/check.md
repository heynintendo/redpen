---
description: Verify Claude Code's latest completion claims with RedPen
allowed-tools: Bash(redpen:*), Bash(true:*)
---

RedPen extracts the success claims from this session's transcript and diffs
each one against real system state. Run it and surface the verdict verbatim.

The trailing `|| true` keeps a `FAIL` verdict from being reported as a command
error: RedPen exits non-zero on failures (so a git hook can gate on it), but
here we only want the rendered verdict — whether or not anything failed.

!`redpen check || true`

Report the result to the user exactly as RedPen rendered it. Do not re-judge
the task yourself, do not re-read the codebase, and do not soften a `FAIL` —
RedPen only marks a claim failed when the evidence contradicts it.

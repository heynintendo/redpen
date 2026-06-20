---
description: Verify Claude Code's latest completion claims with RedPen
allowed-tools: Bash(redpen:*)
---

RedPen extracts the success claims from this session's transcript and diffs
each one against real system state. Run it and surface the verdict verbatim.

!`redpen check`

Report the result to the user exactly as RedPen rendered it. Do not re-judge
the task yourself, do not re-read the codebase, and do not soften a `FAIL` —
RedPen only marks a claim failed when the evidence contradicts it.

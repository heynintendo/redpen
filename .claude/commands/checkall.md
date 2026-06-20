---
description: Deep RedPen audit — verify claims AND reconcile them against the original request
allowed-tools: Bash(redpen:*)
---

`/checkall` runs RedPen's deep pass over this session:

1. deterministic probes gather targeted evidence (same as `/check`),
2. the LLM judge resolves the claims that came back UNVERIFIABLE, judging ONLY
   that gathered evidence — it never re-reads the codebase,
3. a full-request audit reconciles three things: what you actually asked for,
   what Claude said it did, and what the evidence shows — surfacing anything
   requested but silently skipped or left unsubstantiated.

The LLM layer runs on your own Claude Code subscription in headless mode (no API
key, no per-token billing); it needs Claude Code installed and logged in.

!`redpen check --deep`

Report the result verbatim. Do not re-judge the task yourself or re-read the
codebase — surface exactly what RedPen marked, especially the audit gaps.

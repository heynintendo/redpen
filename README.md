![RedPen](docs/banner.png)

# RedPen

**Claude Code says ✅. RedPen tells you whether it's lying.**

Claude Code almost always states what it accomplished — "tests pass", "pushed
to remote", "created the file", "done". RedPen doesn't take its word for it. It
extracts each success claim from Claude Code's own output and diffs it against
real system state. Claim vs. reality. Three verdicts, no hedging.

- **Everything is a claim to verify.** Pulled from Claude Code's transcript, or
  asked ad-hoc (`redpen check "is the push done?"`).
- **Deterministic probes first.** RedPen never re-reads your codebase or
  re-explores — that's the failure mode it exists to catch. Probes gather
  targeted evidence and judge it.
- **Three verdicts only.** `OK` (evidence substantiates the claim), `FAIL`
  (evidence *contradicts* it), `UNVERIFIABLE` (can't tell). It cries `FAIL`
  only on contradiction — never on absent evidence. Precision over recall.
- **Fast.** The deterministic path finishes in under 2 seconds and makes no
  network calls except an explicit `git`/`gh` remote probe. Run it after every
  task.

## Install

```bash
pip install git+https://github.com/heynintendo/redpen
```

## Use

```bash
redpen check                 # extract claims from the latest transcript, verify each
redpen check "is the push done?"   # verify one ad-hoc claim
redpen check --run           # permit re-running tests/build/lint (default is read-only)
redpen check --deep          # add the LLM judge + full-request audit (see below)
redpen check --no-art        # skip the mascot
redpen history               # what was claimed before, and did it hold?
```

Wire it into Claude Code as slash commands — `/check` runs `redpen check`
(fast, deterministic) and `/checkall` runs `redpen check --deep` (deep audit).
See `.claude/commands/`.

### Sample output

```
RedPen —

Marked. 1 claim doesn't hold up:

  ✓  created src/app.py                  src/app.py present (412 bytes)
  ✗  pushed to remote                    2 unpushed commit(s) on this branch
  ✓  tests pass                          `pytest` exited 0 this session
  ⚠  opened a PR                         gh not authenticated — run `gh auth login`

  2 OK · 1 FAIL · 1 UNVERIFIABLE        (0.3s)
```

On a real terminal the header is a colored pixel-art examiner (the mascot in
`docs/`); piped or with `NO_COLOR`/`--no-art` it degrades to the `RedPen —`
title shown above. Exit code is non-zero when any claim `FAIL`s, so you can gate
a hook on it.

## How it works

| Stage | What it does |
|-------|--------------|
| **Claim extractor** | Finds the latest Claude Code transcript for this directory, reads the final message, maps each success assertion to probes. |
| **Probes** | Small, self-contained checks: `git_pushed`, `git_clean`, `file_present`, `tests_pass`, `build_ok`, `lint_clean`, `branch_synced`, `pr_status`, `todos_remaining`, `exit_code_scan`. Each returns a structured evidence dict. |
| **Renderer** | One line per claim, ANSI-colored, with a summary footer. |
| **Ledger** | SQLite at `.redpen/ledger.db` records every verdict so a later session can ask what was claimed before. |
| **Judge** *(--deep)* | Resolves `UNVERIFIABLE` claims from the gathered evidence alone, via one headless `claude -p` turn. Never reads the codebase. |
| **Request audit** *(--deep)* | Decomposes your last request and reconciles asked-for vs. claimed vs. evidenced, flagging silent gaps. |

## Deep mode — `/checkall`

`redpen check` is deterministic and verifies what Claude *claimed*. `redpen
check --deep` (the `/checkall` command) goes further, in three stages:

1. **Deterministic probes** run first — same targeted evidence as `/check`.
2. **The LLM judge** resolves the claims that came back `UNVERIFIABLE`, looking
   *only* at the evidence a probe already gathered (exit codes, git summaries,
   file states). It never re-reads or re-explores your codebase — that slow,
   unreliable re-exploration is the exact failure mode RedPen exists to prevent.
   Precision is preserved: it returns `FAIL` only when the evidence contradicts
   the claim; anything missing or ambiguous stays `UNVERIFIABLE`.
3. **The full-request audit** reconciles three things — what you actually asked
   for, what Claude said it did, and what the evidence shows — and surfaces
   anything **requested but silently skipped or left unsubstantiated**.

```
Request audit — 2 asked-for items unaccounted for:

  ✓ DONE             add the LLM judge layer      evidence supports it
  ✗ SKIPPED          write the migration guide    no claim addressed it
  ⚠ UNSUBSTANTIATED  all tests pass               pytest never ran this session

  1 done · 1 unsubstantiated · 1 skipped
```

### How the LLM layer is funded

The deep layer runs on **your own Claude Code subscription** via headless mode
(`claude -p`) — there is **no API key and no per-token billing**. RedPen spawns
the call with `ANTHROPIC_API_KEY` unset (forcing the subscription) and hooks
disabled (so it can't recursively re-trigger itself). The only requirement is
that **Claude Code is installed and logged in**. If it isn't, `--deep` degrades
gracefully: every claim it can't reach simply stays `UNVERIFIABLE`.

`--deep` is opt-in; plain `redpen check` makes no LLM calls at all. The model is
`sonnet` by default — switch the single `LLM_MODEL` line in `redpen/config.py`
to `haiku` for a faster, cheaper pass.

## Auto-verify hook (opt-in, off by default)

Want RedPen to grade every task automatically? Install a Claude Code **Stop
hook** that runs `redpen check` when a task finishes:

```bash
redpen install-hook     # opt in
redpen uninstall-hook   # opt out — removes exactly what it added
```

It is **strictly opt-in** and safe by design:

- **Deterministic only.** The hook runs plain `redpen check` — **never
  `--deep`** — so it makes no LLM calls: no surprise quota use, no latency. (If
  the hook env is set, `--deep` is refused outright as a second guard.)
- **No recursion.** RedPen's own judge calls spawn `claude -p` with hooks
  disabled, so they can't trigger the hook.
- **Personal and reversible.** It writes to `.claude/settings.local.json` (your
  git-ignored personal settings, not the shared `settings.json`), and
  `uninstall-hook` removes only RedPen's entry, leaving everything else intact.

## Demo

![RedPen demo](docs/demo.gif)

A mixed check (some claims true, some false) then a `--deep` full-request audit.
The recording is scripted and reproducible: `bash docs/gen_demo.sh` (needs
[`vhs`](https://github.com/charmbracelet/vhs) + `ffmpeg`; see `docs/demo.tape`).

## License

MIT.

# RedPen `stress_hard` adversarial report

_Generated 2026-06-21T09:32:43+00:00 • 326 cases • full sequential run 53.0s_

This suite measures how accurately RedPen parses and attributes reality on the inputs a heavy daily Claude Code user creates on huge, long-lived, multi-workflow repos. Ground truth is programmatic: every case records the correct verdict per claim. Divergences are **genuine RedPen findings**, not suite bugs.

## Headline

| metric | value |
| --- | --- |
| total cases | **326** |
| passed (verdict == ground truth) | **325** (99%) |
| diverged | 1 |
| **false-FAIL** (true claim / user-or-other-session edit / absence marked FAIL) | **0** in 0 cases |
| **false-OK** (a genuine lie marked OK) | **0** in 0 cases |
| **misparse** (claim invented or missed) | **1** in 1 case |
| soft mismatches (non-headline) | 0 in 0 cases |
| build / harness errors | 0 |

> These three dimensions are independent, not a partition: a phantom FAIL counts as both misparse and false-FAIL. false-FAIL is driven by ground-truth reality (FAIL is only ever correct on a genuine contradiction); false-OK requires the claim to actually be a lie.

## Latency — deterministic path (redpen `elapsed_seconds`, contention-free)

Across 293 non-`--deep` cases (incl. the 10k-file tree, the ~tens-of-thousands-of-token transcript, and the 300-file sprawl):

- p50 **0.040s** • p95 **0.107s** • p99 **0.141s** • max **0.259s**
- deterministic path stays sub-second: **YES** (p99 < 1.0s)
  - `scale_noise/tree_10k_files`: 0.042s
  - `scale_noise/sprawl_dirty_commit_claim`: 0.107s
  - `scale_noise/huge_transcript_buried_claim`: 0.117s

## Concurrency soak

- distinct temp repos, 16-wide: **90/90** stayed correct (no cross-contamination)
- same-repo ledger contention (16 concurrent `redpen check`): clean-exit=True, verdicts-consistent=True, SQLite integrity=**ok**, rows=96
- same-repo judge-cache contention (12 concurrent `--deep`): clean-exit=True, judge_cache.json valid=**True**

## false-FAIL findings (0)

_none_

## false-OK findings (0)

_none_

## misparse findings (1)

- **`claim_extraction/split_chitchat_final`** — real claims in earlier turn, final is chit-chat (final-msg scoping)
  - misparse: MISSED claim: expected git_pushed [claim made in a non-final turn]
  - redpen returned: (no findings)
  - repro: `.venv/bin/python tests/stress_hard/run_report.py claim_extraction/split_chitchat_final`

## Findings grouped by root cause

- **final-message-only-scoping** (misparse, 1 case): `split_chitchat_final`

## Per-axis breakdown

| axis | cases | passed |
| --- | --- | --- |
| attribution | 14 | 14 |
| claim_extraction | 33 | 32 |
| contradiction | 16 | 16 |
| deep_decomp | 14 | 14 |
| environment | 17 | 17 |
| generated | 225 | 225 |
| scale_noise | 7 | 7 |

## How to reproduce

```sh
# regenerate this report
.venv/bin/python tests/stress_hard/run_report.py
# inspect one case in detail
.venv/bin/python tests/stress_hard/run_report.py contradiction/zero_failed_summary
# run the whole suite under pytest-xdist (concurrency sweep)
REDPEN_STRESS_HARD=1 .venv/bin/python -m pytest tests/stress_hard -n auto
# the tiny real-claude --deep wiring check (off by default)
REDPEN_STRESS_LIVE=1 REDPEN_STRESS_HARD=1 .venv/bin/python -m pytest tests/stress_hard/test_live.py
```


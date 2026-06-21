# RedPen `stress_hard` adversarial report

_Generated 2026-06-21T08:41:34+00:00 • 326 cases • full sequential run 55.1s_

This suite measures how accurately RedPen parses and attributes reality on the inputs a heavy daily Claude Code user creates on huge, long-lived, multi-workflow repos. Ground truth is programmatic: every case records the correct verdict per claim. Divergences are **genuine RedPen findings**, not suite bugs.

## Headline

| metric | value |
| --- | --- |
| total cases | **326** |
| passed (verdict == ground truth) | **307** (94%) |
| diverged | 19 |
| **false-FAIL** (true claim / user-or-other-session edit / absence marked FAIL) | **15** in 13 cases |
| **false-OK** (a genuine lie marked OK) | **1** in 1 case |
| **misparse** (claim invented or missed) | **10** in 6 cases |
| soft mismatches (non-headline) | 0 in 0 cases |
| build / harness errors | 0 |

> These three dimensions are independent, not a partition: a phantom FAIL counts as both misparse and false-FAIL. false-FAIL is driven by ground-truth reality (FAIL is only ever correct on a genuine contradiction); false-OK requires the claim to actually be a lie.

## Latency — deterministic path (redpen `elapsed_seconds`, contention-free)

Across 296 non-`--deep` cases (incl. the 10k-file tree, the ~tens-of-thousands-of-token transcript, and the 300-file sprawl):

- p50 **0.043s** • p95 **0.112s** • p99 **0.243s** • max **0.354s**
- deterministic path stays sub-second: **YES** (p99 < 1.0s)
  - `scale_noise/tree_10k_files`: 0.044s
  - `scale_noise/sprawl_dirty_commit_claim`: 0.153s
  - `scale_noise/huge_transcript_buried_claim`: 0.354s

## Concurrency soak

- distinct temp repos, 16-wide: **90/90** stayed correct (no cross-contamination)
- same-repo ledger contention (16 concurrent `redpen check`): clean-exit=True, verdicts-consistent=True, SQLite integrity=**ok**, rows=96
- same-repo judge-cache contention (12 concurrent `--deep`): clean-exit=True, judge_cache.json valid=**True**

## false-FAIL findings (15)

- **`claim_extraction/negation_not_done_dirty`** — 'not done, uncommitted' must not be graded as a completion claim
  - false-FAIL: PHANTOM FAIL: git_clean 'everything is committed' (invented claim failed)
  - misparse: PHANTOM finding: git_pushed 'everything is pushed' = UNVERIFIABLE (no such claim was made)
  - misparse: PHANTOM finding: git_clean 'everything is committed' = FAIL (no such claim was made)
  - misparse: PHANTOM finding: tests_pass 'tests pass' = UNVERIFIABLE (no such claim was made)
  - misparse: PHANTOM finding: todos_remaining 'no unfinished stubs left behind' = UNVERIFIABLE (no such claim was made)
  - misparse: PHANTOM finding: contradiction_scan 'no failed step was called done' = OK (no such claim was made)
  - redpen returned: git_pushed=UNVERIFIABLE, git_clean=FAIL, tests_pass=UNVERIFIABLE, todos_remaining=UNVERIFIABLE, contradiction_scan=OK
  - repro: `.venv/bin/python tests/stress_hard/run_report.py claim_extraction/negation_not_done_dirty`
- **`attribution/toctou_git_clean_baseline_0`** — TOCTOU user edit dirties tree (baseline=False) -> git_clean must not FAIL
  - false-FAIL: git_clean 'Committed everything; the working tree is clean' -> FAIL, but the claim is true; agent's commit claim was true at finish; later edit is the user's
  - redpen returned: git_clean=FAIL, git_pushed=UNVERIFIABLE, tests_pass=UNVERIFIABLE, todos_remaining=UNVERIFIABLE, contradiction_scan=OK
  - repro: `.venv/bin/python tests/stress_hard/run_report.py attribution/toctou_git_clean_baseline_0`
- **`attribution/toctou_git_clean_baseline_1`** — TOCTOU user edit dirties tree (baseline=True) -> git_clean must not FAIL
  - false-FAIL: git_clean 'Committed everything; the working tree is clean' -> FAIL, but the claim is true; agent's commit claim was true at finish; later edit is the user's
  - redpen returned: git_clean=FAIL, git_pushed=UNVERIFIABLE, tests_pass=UNVERIFIABLE, todos_remaining=UNVERIFIABLE, contradiction_scan=OK
  - repro: `.venv/bin/python tests/stress_hard/run_report.py attribution/toctou_git_clean_baseline_1`
- **`contradiction/fail_then_pass_rerun`** — test fails early then passes on re-run -> must not FAIL
  - false-FAIL: tests_pass 'Fixed it — all tests pass now' -> FAIL, but the claim is true; the LAST run passed; scanning all events flags the early failure
  - redpen returned: tests_pass=FAIL
  - repro: `.venv/bin/python tests/stress_hard/run_report.py contradiction/fail_then_pass_rerun`
- **`contradiction/fail_then_pass_build`** — build fails then succeeds on re-run -> must not FAIL
  - false-FAIL: build_ok 'Build is green now' -> FAIL, but the claim is true
  - redpen returned: build_ok=FAIL
  - repro: `.venv/bin/python tests/stress_hard/run_report.py contradiction/fail_then_pass_build`
- **`contradiction/benign_grep_nomatch`** — benign non-zero exit ('grep -r TODO src') + done -> must not FAIL
  - false-FAIL: contradiction_scan 'no failed step was called done' -> FAIL, but the claim is true; grep -r TODO src exiting non-zero is normal, not a failure
  - redpen returned: git_pushed=UNVERIFIABLE, git_clean=OK, tests_pass=UNVERIFIABLE, todos_remaining=UNVERIFIABLE, contradiction_scan=FAIL
  - repro: `.venv/bin/python tests/stress_hard/run_report.py contradiction/benign_grep_nomatch`
- **`contradiction/benign_diff`** — benign non-zero exit ('git diff --quiet') + done -> must not FAIL
  - false-FAIL: contradiction_scan 'no failed step was called done' -> FAIL, but the claim is true; git diff --quiet exiting non-zero is normal, not a failure
  - redpen returned: git_pushed=UNVERIFIABLE, git_clean=OK, tests_pass=UNVERIFIABLE, todos_remaining=UNVERIFIABLE, contradiction_scan=FAIL
  - repro: `.venv/bin/python tests/stress_hard/run_report.py contradiction/benign_diff`
- **`contradiction/benign_test_builtin`** — benign non-zero exit ('test -f optional.cfg') + done -> must not FAIL
  - false-FAIL: tests_pass 'tests pass' -> FAIL, but the claim is unprovable (not a contradiction)
  - false-FAIL: contradiction_scan 'no failed step was called done' -> FAIL, but the claim is true; test -f optional.cfg exiting non-zero is normal, not a failure
  - redpen returned: git_pushed=UNVERIFIABLE, git_clean=OK, tests_pass=FAIL, todos_remaining=UNVERIFIABLE, contradiction_scan=FAIL
  - repro: `.venv/bin/python tests/stress_hard/run_report.py contradiction/benign_test_builtin`
- **`contradiction/benign_tool_exit3`** — a tool exiting 3 by design + done -> must not FAIL
  - false-FAIL: contradiction_scan 'no failed step was called done' -> FAIL, but the claim is true
  - redpen returned: git_pushed=UNVERIFIABLE, git_clean=OK, tests_pass=UNVERIFIABLE, todos_remaining=UNVERIFIABLE, contradiction_scan=FAIL
  - repro: `.venv/bin/python tests/stress_hard/run_report.py contradiction/benign_tool_exit3`
- **`contradiction/sig_in_cat_traceback`** — cat of a log fixture containing a Traceback + done -> must not FAIL
  - false-FAIL: contradiction_scan 'no failed step was called done' -> FAIL, but the claim is true; the Traceback is fixture content, not a real failure
  - redpen returned: git_pushed=UNVERIFIABLE, git_clean=OK, tests_pass=UNVERIFIABLE, todos_remaining=UNVERIFIABLE, contradiction_scan=FAIL
  - repro: `.venv/bin/python tests/stress_hard/run_report.py contradiction/sig_in_cat_traceback`
- **`contradiction/sig_in_echo_failed`** — echo of the word FAILED in a help string + done -> must not FAIL
  - false-FAIL: tests_pass 'tests pass' -> FAIL, but the claim is unprovable (not a contradiction)
  - false-FAIL: contradiction_scan 'no failed step was called done' -> FAIL, but the claim is true
  - redpen returned: git_pushed=UNVERIFIABLE, git_clean=OK, tests_pass=FAIL, todos_remaining=UNVERIFIABLE, contradiction_scan=FAIL
  - repro: `.venv/bin/python tests/stress_hard/run_report.py contradiction/sig_in_echo_failed`
- **`contradiction/zero_failed_summary`** — '0 failed' in a passing summary read as a failure
  - false-FAIL: tests_pass 'All tests pass' -> FAIL, but the claim is true; '0 failed' matches the failure regex though nothing failed
  - redpen returned: tests_pass=FAIL
  - repro: `.venv/bin/python tests/stress_hard/run_report.py contradiction/zero_failed_summary`
- **`contradiction/build_sig_in_cat`** — cat of TS docs containing 'error TS' + build claim -> must not FAIL
  - false-FAIL: build_ok 'The build works now' -> FAIL, but the claim is true; 'error TS2304' is documentation, not a compile error
  - redpen returned: build_ok=FAIL
  - repro: `.venv/bin/python tests/stress_hard/run_report.py contradiction/build_sig_in_cat`

## false-OK findings (1)

- **`attribution/other_session_edit`** — file changed by a different session, claimed by this one
  - false-OK: file_present 'wrote shared.py' -> OK, but the claim is a genuine lie; this session never wrote it; git cannot attribute per-session
  - redpen returned: file_present=OK
  - repro: `.venv/bin/python tests/stress_hard/run_report.py attribution/other_session_edit`

## misparse findings (10)

- **`claim_extraction/negation_not_done_dirty`** — 'not done, uncommitted' must not be graded as a completion claim
  - false-FAIL: PHANTOM FAIL: git_clean 'everything is committed' (invented claim failed)
  - misparse: PHANTOM finding: git_pushed 'everything is pushed' = UNVERIFIABLE (no such claim was made)
  - misparse: PHANTOM finding: git_clean 'everything is committed' = FAIL (no such claim was made)
  - misparse: PHANTOM finding: tests_pass 'tests pass' = UNVERIFIABLE (no such claim was made)
  - misparse: PHANTOM finding: todos_remaining 'no unfinished stubs left behind' = UNVERIFIABLE (no such claim was made)
  - misparse: PHANTOM finding: contradiction_scan 'no failed step was called done' = OK (no such claim was made)
  - redpen returned: git_pushed=UNVERIFIABLE, git_clean=FAIL, tests_pass=UNVERIFIABLE, todos_remaining=UNVERIFIABLE, contradiction_scan=OK
  - repro: `.venv/bin/python tests/stress_hard/run_report.py claim_extraction/negation_not_done_dirty`
- **`claim_extraction/split_chitchat_final`** — real claims in earlier turn, final is chit-chat (final-msg scoping)
  - misparse: MISSED claim: expected git_pushed [claim made in a non-final turn]
  - redpen returned: (no findings)
  - repro: `.venv/bin/python tests/stress_hard/run_report.py claim_extraction/split_chitchat_final`
- **`claim_extraction/sarcasm_not_done`** — sarcastic non-claim
  - misparse: PHANTOM finding: git_pushed 'Yeah, because 'just push it' is ever that simple' = UNVERIFIABLE (no such claim was made)
  - redpen returned: git_pushed=UNVERIFIABLE
  - repro: `.venv/bin/python tests/stress_hard/run_report.py claim_extraction/sarcasm_not_done`
- **`claim_extraction/code_block_comment`** — trigger words inside a code fence become a claim
  - misparse: PHANTOM finding: tests_pass 'tests pass here' = UNVERIFIABLE (no such claim was made)
  - redpen returned: unmapped=UNVERIFIABLE, tests_pass=UNVERIFIABLE
  - repro: `.venv/bin/python tests/stress_hard/run_report.py claim_extraction/code_block_comment`
- **`claim_extraction/code_block_push`** — git command in a code fence becomes a push claim
  - misparse: PHANTOM finding: git_pushed 'git push origin main' = UNVERIFIABLE (no such claim was made)
  - redpen returned: git_pushed=UNVERIFIABLE
  - repro: `.venv/bin/python tests/stress_hard/run_report.py claim_extraction/code_block_push`
- **`claim_extraction/created_x_and_y`** — 'Created X and Y' extracts only X (misses Y)
  - misparse: MISSED claim: expected file_present ~'src/y.py' [second path after 'and' has no verb -> missed]
  - redpen returned: file_present=OK
  - repro: `.venv/bin/python tests/stress_hard/run_report.py claim_extraction/created_x_and_y`

## Findings grouped by root cause

- **benign-nonzero-exit** (false_FAIL, 4 cases): `benign_grep_nomatch`, `benign_diff`, `benign_test_builtin`, `benign_tool_exit3`
- **git_clean-no-attribution** (false_FAIL, 2 cases): `toctou_git_clean_baseline_0`, `toctou_git_clean_baseline_1`
- **negation-blind-narration** (false_FAIL, 1 case): `negation_not_done_dirty`
- **not-last-run-aware** (false_FAIL, 2 cases): `fail_then_pass_rerun`, `fail_then_pass_build`
- **signature-in-benign-output** (false_FAIL, 3 cases): `sig_in_cat_traceback`, `sig_in_echo_failed`, `build_sig_in_cat`
- **zero-failed-regex** (false_FAIL, 1 case): `zero_failed_summary`
- **cross-session-git-attribution** (false_OK, 1 case): `other_session_edit`
- **conjunction-misses-second-path** (misparse, 1 case): `created_x_and_y`
- **extracts-from-code-fence** (misparse, 2 cases): `code_block_comment`, `code_block_push`
- **extracts-from-quote** (misparse, 1 case): `sarcasm_not_done`
- **final-message-only-scoping** (misparse, 1 case): `split_chitchat_final`

## Per-axis breakdown

| axis | cases | passed |
| --- | --- | --- |
| attribution | 14 | 11 |
| claim_extraction | 33 | 27 |
| contradiction | 16 | 6 |
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


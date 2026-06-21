# `stress_hard` — RedPen adversarial accuracy suite

A second, much harder RedPen stress suite. It is fully isolated from
`tests/stress/` (own fixtures, own temp dirs, own conftest, own report, no shared
files touched) so the two can run in parallel in separate sessions.

Where the unit tests check individual probes, this suite attacks the conditions
of a **heavy daily Claude Code user on huge, long-lived, multi-workflow repos** —
where the hard part is correctly *parsing and attributing reality*, not rendering
a verdict. Every case carries **programmatic ground truth** (the exact expected
verdict per claim), so correctness is checkable, and the run is seeded and
reproducible.

## What it measures

Three error classes, tallied independently (see `last_report.md`):

- **false-FAIL** — a true claim, a user/other-session edit, or mere absence of
  evidence marked `FAIL`. The cardinal sin (crying wolf).
- **false-OK** — a genuine lie marked `OK` (a rubber stamp).
- **misparse** — claim extraction error: a phantom claim RedPen invented, or a
  real claim it missed. At scale this is the real risk.

Ground truth is the *correct attribution of reality by a careful reviewer*, not
"whatever RedPen currently outputs" — so divergences are genuine findings.

## Layout

```
harness/      model (EF/Case/classify), builders (repo+transcript+RNG),
              fake_bins (deterministic claude/gh), runner, run_all
cases/        one module per adversarial axis + a seeded generator matrix
              registry.py aggregates them all (order-stable, dedup-checked)
known_findings.py   the recorded baseline of genuine divergences (regression guard)
test_stress_hard.py per-case verdict + robustness, parametrized (xdist-friendly)
test_concurrency.py distinct-repo soak + same-repo ledger/judge-cache integrity
test_latency.py     deterministic path stays sub-second (percentiles)
test_live.py        tiny real-`claude` --deep wiring check (off by default)
run_report.py       generates last_report.md (+ single-case debugger)
```

## Adversarial axes

`claim_extraction` (hedged/sarcastic/retracted/code-fence/multilingual/weird-path
claims), `attribution` (pre-existing / other-session / TOCTOU / created-then-deleted
/ symlink / artifact / sub-agent roll-up), `contradiction` (fail-then-pass, flaky,
benign non-zero exits, signatures inside fixtures/logs, ANSI), `scale_noise`
(monorepos, 10k-file trees, huge transcripts, interleaved sub-agents), `deep_decomp`
(the `--deep` judge seam + request audit, with a mocked deterministic `claude`),
`environment` (no network, gh missing/unauthed, detached HEAD, mid-merge/rebase,
submodules, worktrees, corrupted/empty transcripts, unwritable state, non-git), and
a large seeded `generated` control matrix.

## Running

```sh
# install the extra deps (kept out of pyproject.toml on purpose)
.venv/bin/python -m pip install -r tests/stress_hard/requirements.txt

# generate the report (canonical artifact)
.venv/bin/python tests/stress_hard/run_report.py

# inspect one case in detail
.venv/bin/python tests/stress_hard/run_report.py contradiction/zero_failed_summary

# pytest concurrency sweep (gated behind REDPEN_STRESS_HARD so a plain
# `pytest` elsewhere never triggers this heavy suite)
REDPEN_STRESS_HARD=1 .venv/bin/python -m pytest tests/stress_hard -n auto

# tiny real-claude --deep wiring check (spends a little quota; off by default)
REDPEN_STRESS_LIVE=1 REDPEN_STRESS_HARD=1 .venv/bin/python -m pytest tests/stress_hard/test_live.py
```

The LLM judge is mocked by a deterministic fake `claude` on `PATH` for the
`--deep` cases, so they are reproducible and spend no quota; `test_live.py`
exercises one real call to confirm the wiring.

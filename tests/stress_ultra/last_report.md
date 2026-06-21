# RedPen `stress_ultra` report

_Generated 2026-06-21T10:30:50+00:00 • 288 verdict cases + 121 concurrency scenario-runs = 409 total • sequential verdict pass 52.9s_

The one-in-a-million failures that burn a corporate user. Ground truth is unarguable; where a real input is inherently ambiguous the asserted verdict is the fail-safe UNVERIFIABLE. Spread evenly across four dimensions.

## Headline

| metric | value |
| --- | --- |
| total cases (verdict + concurrency) | **409** |
| verdict cases passed | **288 / 288** |
| **false-FAIL** (true/ambiguous claim marked FAIL) | **0** in 0 cases |
| **false-OK** (a genuine lie marked OK) | **0** in 0 cases |
| **misparse** (claim invented or missed) | **0** in 0 cases |
| build / harness errors | 0 |

## Per-dimension breakdown

| dimension | cases | passed | false-FAIL | false-OK | misparse |
| --- | --- | --- | --- | --- | --- |
| A · false-OK (the silent betrayal) | 123 | 123 | 0 | 0 | 0 |
| B · claim misparse at scale / noise | 86 | 86 | 0 | 0 | 0 |
| D · attribution & contradiction at the seams | 79 | 79 | 0 | 0 | 0 |
| C · concurrency / state corruption | 121 | all clean | — | — | — |

## Latency — deterministic path (redpen `elapsed_seconds`, contention-free)

Across 279 non-`--deep` verdict cases (incl. the ~hundreds-of-thousands-of-token transcripts):

- p50 **0.046s** • p95 **0.135s** • p99 **0.146s** • max **0.164s**
- sub-second p99: **YES**

## Concurrency soak (dimension C)

- distinct repos 16-wide: **60/60** stayed correct (no contamination)
- same-repo check contention (40 procs): clean-exit=True, verdicts-consistent=True, SQLite integrity=**ok**, rows=240
- `--deep` judge-cache thundering herd (20 procs, identical evidence): clean-exit=True, consistent=True, cache valid=**True**
- recovery from corrupt partial state: **True**
- result: **clean — no corruption, no crashes, no contamination**

## false-FAIL findings (0)

_none_

## false-OK findings (0)

_none_


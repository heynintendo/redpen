_generated 2026-06-21T07:27:47+00:00_

# RedPen stress report

- seed: `1234` (reproducible)
- cases: **320**  ·  clean: **320**  ·  errors: **0**
- expectations: 466  ·  exact pass: 466  ·  soft mismatch: 0

## The two unforgivable failures

- **false FAIL** (true claim marked FAIL): **0**
- **false OK** (a real lie marked OK): **0**
- leaks (non-claim produced a verdict): 0
- missing (expected finding absent): 0

## Latency (deterministic fast path)

- p50 31 ms  ·  p90 69 ms  ·  p99 108 ms  ·  max 158 ms
- sub-second p99: yes

## Broken cases

None — all cases matched ground truth.

## Concurrency soak

- 36 parallel `redpen check` runs across 24 repos (16 workers); project 0 hammered concurrently.
- crashes: 0  ·  corruption / cross-contamination: 0
- result: clean — no corruption, no crashes, no cross-contamination

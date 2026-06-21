![RedPen](docs/banner.png)

# RedPen

**Claude Code says it's done. RedPen checks whether that's actually true.**

RedPen reads the completion claims an agent makes ("tests pass", "pushed",
"created the file", "done") and verifies each one against real system state.

![RedPen catching a false "all tests pass" claim, then passing honest work](docs/demo.gif)

## The problem

Coding agents narrate success. They say "done", "tests pass", "pushed to
remote", and most of the time they are right. Sometimes they are not, and you
do not find out until it bites you: in CI, in review, or in production. RedPen
is the check that catches it at the source, grading the agent's own claims
against what actually happened.

## Install

```bash
git clone https://github.com/heynintendo/redpen && cd redpen && uv tool install .
cp .claude/commands/*.md ~/.claude/commands/
```

Now type `/check` in any Claude Code session. (Needs [uv](https://docs.astral.sh/uv/);
`pipx install .` works too.)

## Use

`/check` is the everyday command: fast, deterministic, no LLM. It pulls the
claims out of the current session and grades each one.

`/checkall` adds the deep pass. An LLM judge resolves the claims a probe left
unconfirmed, looking only at evidence already gathered and never re-reading your
code, then audits your whole request to surface anything you asked for that got
quietly skipped.

```
1 verified · 1 can't confirm · 1 failed
Look at the failed line first.

   1. [OK]      wrote src/app.py     app.py is there (412 bytes)
   2. [FAIL]    the tests pass       pytest reported 1 failed this session
   3. [ ? ]     pushed to origin     no upstream set, so I can't tell if it's pushed
```

On a real terminal the header is a colored examiner and the markers are
`● OK` / `● FAIL` / `▲ UNSURE`; piped or under `NO_COLOR` it degrades to the
clean monochrome above. The exit code is non-zero on any failure, so a git hook
can gate on it.

Every line is numbered. `redpen explain <n>` prints the claim, the probe, the
exact commands run, and the raw evidence behind verdict `n`. `redpen history`
shows what was claimed in past sessions and whether it held.

## How it works

RedPen extracts each concrete claim from the agent's final message and maps it
to a small, self-contained probe that gathers targeted evidence: did this file
land in the session's changed set, did pytest actually run and pass, is the
branch really ahead of its upstream, does the agent's own captured output
contain a failure it narrated over. Then it returns one of three verdicts, with
no hedging:

- **verified**, the evidence substantiates the claim.
- **failed**, the evidence contradicts it.
- **can't confirm**, there is not enough evidence either way.

The precision rule is absolute: RedPen marks something **failed only when the
evidence contradicts the claim**, never on missing evidence. A file it cannot
attribute to this session, a test that never ran, a push it cannot reach: all of
those are "can't confirm", not "failed". It would rather stay quiet than cry
wolf.

It runs entirely on your machine. The deep pass uses **your own Claude Code
subscription** in headless mode, so there is no API key and no per-token
billing; everything else is local. No telemetry, no hosted service, nothing
leaves your laptop. The deterministic path finishes in well under a second.

## Why trust the verdict

RedPen is hardened against more than a thousand adversarial cases across three
stress suites (buried failures, masked exit codes, lying recaps, concurrent
runs, hostile git states), with **zero false-FAILs and zero false-OKs** across
every graded case. The bar is simple and non-negotiable: never mark honest work
as failed, never wave a real lie through as verified.

## More

- **Works without git.** The transcript and filesystem resolve "created X" and
  "tests pass" with no repo. Git-only probes are skipped where there is no repo.
- **Custom rules** (`.redpen.yml`). Map a claim pattern to your own verification
  command for stack-specific claims like deploys, migrations, or codegen. See
  [`docs/redpen.example.yml`](docs/redpen.example.yml).
- **Auto-verify hook** (`redpen install-hook`). Grade every task automatically
  via an opt-in, deterministic-only Claude Code Stop hook. `redpen
  uninstall-hook` removes exactly what it added.
- **Never re-explores.** Probes verify from gathered evidence and the
  transcript; RedPen never re-reads or re-runs your codebase to "check", because
  that slow, unreliable re-exploration is the exact failure mode it exists to
  catch. `--run` re-executes tests or builds only when you ask, and is off by
  default.

## License

MIT.

Contributions are welcome. The test suites are the spec, so a change is ready
when `pytest` is green and all three stress suites stay at zero false-FAILs and
zero false-OKs (`./redpen-stress`, plus the `tests/stress_hard` and
`tests/stress_ultra` reports).

"""Run the labelled cases and score them against ground truth.

The two metrics that matter are unforgivable failures:
  * false FAIL  -- a claim that is actually true (or genuinely unverifiable),
                   marked FAIL.
  * false OK    -- a real lie, marked OK.
Everything else (over/under-confident UNVERIFIABLE) is a "soft" mismatch.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from redpen.cli import main as redpen_main

from .cases import Case, generate, write_transcript


@contextlib.contextmanager
def _chdir(d: Path):
    old = os.getcwd()
    os.chdir(d)
    try:
        yield
    finally:
        os.chdir(old)


def _stub_llm() -> None:
    """Never make a real LLM call from the harness; --deep falls back cleanly."""
    import redpen.judge as judge

    judge._run_claude = lambda *a, **k: (127, "", "stubbed in stress harness")


def run_check(workdir: Path, transcript: Path, deep: bool) -> tuple[dict, float]:
    argv = ["check", "--transcript", str(transcript), "--no-color", "--no-art", "--json"]
    if deep:
        argv.append("--deep")
    buf = io.StringIO()
    t0 = time.perf_counter()
    with _chdir(workdir), contextlib.redirect_stdout(buf):
        redpen_main(argv)
    dt = time.perf_counter() - t0
    out = buf.getvalue()
    data = json.loads(out[out.find("{"):]) if "{" in out else {"findings": []}
    return data, dt


@dataclass
class Issue:
    case: str
    kind: str       # false_fail | false_ok | leak | missing | soft | error
    match: str
    detail: str
    tags: list[str] = field(default_factory=list)


def evaluate(case: Case, findings: list[dict]) -> tuple[dict, list[Issue]]:
    counts = {"pass": 0, "false_fail": 0, "false_ok": 0, "leak": 0, "missing": 0, "soft": 0}
    issues: list[Issue] = []
    table = [(f.get("probe", ""), (f.get("subject", "") + " " + f.get("detail", "")).lower(), f.get("verdict", ""))
             for f in findings]

    def find(match: str):
        ml = match.lower()
        for probe, text, verdict in table:
            if match == probe or ml in text:
                return verdict
        return None

    for e in case.expects:
        if e.expect == "ABSENT":
            if find(e.match) is not None:
                counts["leak"] += 1
                issues.append(Issue(case.name, "leak", e.match, "non-claim produced a verdict", case.tags))
            else:
                counts["pass"] += 1
            continue
        got = find(e.match)
        if got is None:
            counts["missing"] += 1
            issues.append(Issue(case.name, "missing", e.match, f"no finding (wanted {e.expect})", case.tags))
            continue
        if got == e.expect:
            counts["pass"] += 1
        elif e.polarity in ("true", "unprovable") and got == "FAIL":
            counts["false_fail"] += 1
            issues.append(Issue(case.name, "false_fail", e.match, f"true claim marked FAIL", case.tags))
        elif e.polarity == "lie" and got == "OK":
            counts["false_ok"] += 1
            issues.append(Issue(case.name, "false_ok", e.match, f"lie marked OK", case.tags))
        else:
            counts["soft"] += 1
            issues.append(Issue(case.name, "soft", e.match, f"wanted {e.expect}, got {got}", case.tags))
    return counts, issues


@dataclass
class Report:
    n_cases: int = 0
    cases_clean: int = 0
    counts: dict = field(default_factory=lambda: {"pass": 0, "false_fail": 0, "false_ok": 0, "leak": 0, "missing": 0, "soft": 0})
    issues: list[Issue] = field(default_factory=list)
    times: list[float] = field(default_factory=list)
    errors: list[Issue] = field(default_factory=list)
    seed: int = 0


def _pct(times: list[float], q: float) -> float:
    if not times:
        return 0.0
    s = sorted(times)
    k = min(len(s) - 1, int(round(q * (len(s) - 1))))
    return s[k]


def run_all(n: int = 320, seed: int = 1234, keep_root: Path | None = None) -> Report:
    _stub_llm()
    cases = generate(n=n, seed=seed)
    rep = Report(n_cases=len(cases), seed=seed)
    root = keep_root or Path(tempfile.mkdtemp(prefix="redpen-stress-"))
    tdir = root / "_transcripts"
    tdir.mkdir(parents=True, exist_ok=True)
    for idx, case in enumerate(cases):
        d = root / f"{idx:04d}_{case.name}"
        d.mkdir(parents=True, exist_ok=True)
        try:
            case.setup(d)
            # Keep the transcript OUT of the project dir, or it shows up as an
            # untracked file and (correctly) dirties a git_clean check.
            tpath = tdir / f"{idx:04d}.jsonl"
            write_transcript(tpath, case.transcript(d))
            data, dt = run_check(d, tpath, case.deep)
            rep.times.append(dt)
            counts, issues = evaluate(case, data.get("findings", []))
        except Exception as exc:  # noqa: BLE001 -- a crash is itself a finding
            rep.errors.append(Issue(case.name, "error", "-", repr(exc)[:200], case.tags))
            continue
        for k, v in counts.items():
            rep.counts[k] += v
        rep.issues.extend(issues)
        if not any(i.kind in ("false_fail", "false_ok", "leak", "missing") for i in issues):
            rep.cases_clean += 1
    return rep


def render_report(rep: Report) -> str:
    p50, p90, p99 = _pct(rep.times, 0.50), _pct(rep.times, 0.90), _pct(rep.times, 0.99)
    pmax = max(rep.times) if rep.times else 0.0
    total_expects = sum(rep.counts.values())
    lines = [
        "# RedPen stress report",
        "",
        f"- seed: `{rep.seed}` (reproducible)",
        f"- cases: **{rep.n_cases}**  ·  clean: **{rep.cases_clean}**  ·  errors: **{len(rep.errors)}**",
        f"- expectations: {total_expects}  ·  exact pass: {rep.counts['pass']}  ·  soft mismatch: {rep.counts['soft']}",
        "",
        "## The two unforgivable failures",
        "",
        f"- **false FAIL** (true claim marked FAIL): **{rep.counts['false_fail']}**",
        f"- **false OK** (a real lie marked OK): **{rep.counts['false_ok']}**",
        f"- leaks (non-claim produced a verdict): {rep.counts['leak']}",
        f"- missing (expected finding absent): {rep.counts['missing']}",
        "",
        "## Latency (deterministic fast path)",
        "",
        f"- p50 {p50 * 1000:.0f} ms  ·  p90 {p90 * 1000:.0f} ms  ·  p99 {p99 * 1000:.0f} ms  ·  max {pmax * 1000:.0f} ms",
        f"- sub-second p99: {'yes' if p99 < 1.0 else 'NO'}",
        "",
    ]
    bad = [i for i in rep.issues if i.kind in ("false_fail", "false_ok", "leak", "missing")] + rep.errors
    lines.append("## Broken cases" if bad else "## Broken cases\n\nNone — all cases matched ground truth.")
    for i in bad[:50]:
        lines.append(f"- `{i.case}` — **{i.kind}** on `{i.match}`: {i.detail}  _(tags: {', '.join(i.tags)})_")
    if bad:
        lines.append("")
        lines.append(f"Reproduce: `python -m tests.stress --seed {rep.seed}` (cases are deterministic).")
    return "\n".join(lines) + "\n"


def print_summary(rep: Report) -> None:
    p99 = _pct(rep.times, 0.99)
    print(f"cases={rep.n_cases} clean={rep.cases_clean} errors={len(rep.errors)} "
          f"| false_FAIL={rep.counts['false_fail']} false_OK={rep.counts['false_ok']} "
          f"leak={rep.counts['leak']} missing={rep.counts['missing']} soft={rep.counts['soft']} "
          f"| p99={p99 * 1000:.0f}ms")

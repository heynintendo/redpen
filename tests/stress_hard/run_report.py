"""Generate tests/stress_hard/last_report.md.

Runs the whole suite contention-free (for accurate latency), tallies the three
headline error classes with minimal repros, measures latency percentiles, and
runs a quick concurrency soak. Also a single-case debugger:

    .venv/bin/python tests/stress_hard/run_report.py            # full report
    .venv/bin/python tests/stress_hard/run_report.py <case-id>  # one case, verbose
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cases.registry import all_cases  # noqa: E402
from harness.builders import TB, commit_all, make_repo, write_file  # noqa: E402
from harness.fake_bins import controlled_path, make_bin_dir  # noqa: E402
from harness.run_all import run_all  # noqa: E402
from harness.runner import run_case, run_redpen  # noqa: E402
from known_findings import KNOWN_DIVERGENCES  # noqa: E402

HERE = Path(__file__).resolve().parent
REPORT = HERE / "last_report.md"


def _n_cases(n):
    return f"{n} case" + ("s" if n != 1 else "")


def _pct(values, p):
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


# --- single-case debugger ----------------------------------------------------
def debug_one(cid):
    cases = {c.cid: c for c in all_cases()}
    if cid not in cases:
        print(f"no such case: {cid}")
        return 2
    import tempfile
    res = run_case(cases[cid], Path(tempfile.mkdtemp(prefix="redpen_case_")))
    print(f"case:    {cid}")
    print(f"title:   {cases[cid].title}")
    print(f"passed:  {res.passed}   exit={res.exit_code}   elapsed={res.elapsed}s   wall={res.wall_ms:.0f}ms")
    if res.error:
        print(f"error:   {res.error}")
    for k in ("false_fail", "false_ok", "misparse", "soft"):
        for m in getattr(res, k):
            print(f"  {k}: {m}")
    print("  actual findings:")
    for f in res.actual:
        print(f"    {f['probe']:18} {f['verdict']:13} {f['subject']!r}")
    return 0


# --- concurrency soak --------------------------------------------------------
def run_soak():
    out = {}
    # distinct repos, 16-wide
    cases = [c for c in all_cases()
             if c.cid not in KNOWN_DIVERGENCES and not c.deep
             and c.axis in ("generated", "environment", "attribution", "scale_noise")][:90]
    res = run_all(cases, jobs=16)
    out["distinct_total"] = len(res)
    out["distinct_correct"] = sum(1 for r in res if r.passed)

    # same-repo ledger contention
    import tempfile
    base = Path(tempfile.mkdtemp(prefix="redpen_soak_"))
    root = make_repo(base / "shared", {"README.md": "# x\n"})
    write_file(root, "app.py", "def app():\n    return 1\n")
    commit_all(root, "app")
    t = TB(cwd=root)
    t.user("create app.py and wrap up")
    t.write("app.py")
    t.assistant("Created app.py. Done.")
    tp = t.write_to(base / "t.jsonl")
    with ThreadPoolExecutor(max_workers=16) as ex:
        outs = list(ex.map(lambda _: run_redpen(root, transcript=tp, home=base / "_home"), range(16)))
    out["ledger_procs"] = len(outs)
    out["ledger_clean_exit"] = all(o["rc"] in (0, 1) and not o["timeout"] and o["data"] for o in outs)
    out["ledger_consistent"] = len({
        tuple(sorted((f["probe"], f["verdict"]) for f in o["data"]["findings"])) for o in outs
    }) == 1
    db = root / ".redpen" / "ledger.db"
    conn = sqlite3.connect(str(db))
    try:
        out["ledger_integrity"] = conn.execute("PRAGMA integrity_check").fetchone()[0]
        out["ledger_rows"] = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    finally:
        conn.close()

    # same-repo judge-cache contention (--deep, fake claude)
    droot = make_repo(base / "deep", {"README.md": "# x\n"})
    bind = make_bin_dir(base, claude=True)
    dt = TB(cwd=droot)
    dt.user("refactor the parser")
    dt.assistant("Refactored the parser internals.")
    dtp = dt.write_to(base / "dt.jsonl")
    env = {"PATH": controlled_path(bind), "REDPEN_LLM_MODEL": "mock",
           "REDPEN_FAKE_VERDICT": "OK", "REDPEN_FAKE_AUDIT": "[]"}
    with ThreadPoolExecutor(max_workers=12) as ex:
        douts = list(ex.map(
            lambda _: run_redpen(droot, transcript=dtp, extra_args=("--deep",), env_extra=env, home=base / "_dhome"),
            range(12)))
    out["deep_procs"] = len(douts)
    out["deep_clean_exit"] = all(o["rc"] in (0, 1) and not o["timeout"] and o["data"] for o in douts)
    cache = droot / ".redpen" / "judge_cache.json"
    try:
        json.loads(cache.read_text())
        out["judge_cache_valid"] = True
    except Exception as exc:  # noqa: BLE001
        out["judge_cache_valid"] = f"CORRUPT: {exc}"
    return out


# --- report ------------------------------------------------------------------
def _repro_block(results_by_cid, cid):
    r = results_by_cid[cid]
    lines = [f"- **`{cid}`** — {r.title}"]
    for k, label in (("false_fail", "false-FAIL"), ("false_ok", "false-OK"), ("misparse", "misparse")):
        for m in getattr(r, k):
            lines.append(f"  - {label}: {m}")
    actual = ", ".join(f"{f['probe']}={f['verdict']}" for f in r.actual) or "(no findings)"
    lines.append(f"  - redpen returned: {actual}")
    lines.append(f"  - repro: `.venv/bin/python tests/stress_hard/run_report.py {cid}`")
    return "\n".join(lines)


def main():
    cases = all_cases()
    started = time.perf_counter()
    results = run_all(cases, jobs=1)  # contention-free for accurate elapsed
    wall = time.perf_counter() - started
    by_cid = {r.cid: r for r in results}

    n = len(results)
    passed = [r for r in results if r.passed]
    errors = [r for r in results if r.error]
    ff = [r for r in results if r.false_fail]
    fo = [r for r in results if r.false_ok]
    mp = [r for r in results if r.misparse]
    soft = [r for r in results if r.soft and not (r.false_fail or r.false_ok or r.misparse)]

    ff_count = sum(len(r.false_fail) for r in results)
    fo_count = sum(len(r.false_ok) for r in results)
    mp_count = sum(len(r.misparse) for r in results)

    non_deep_elapsed = [r.elapsed for r in results if r.elapsed is not None
                        and not by_cid_case(cases, r.cid).deep]
    p50, p95, p99, mx = (_pct(non_deep_elapsed, 50), _pct(non_deep_elapsed, 95),
                         _pct(non_deep_elapsed, 99), max(non_deep_elapsed) if non_deep_elapsed else 0)

    # axis + tag breakdowns
    axes = {}
    for r in results:
        a = r.axis
        axes.setdefault(a, [0, 0])
        axes[a][0] += 1
        axes[a][1] += 1 if r.passed else 0

    # root-cause grouping from the recorded baseline
    roots = {}
    for cid, (cat, root) in KNOWN_DIVERGENCES.items():
        roots.setdefault((cat, root), []).append(cid)

    soak = run_soak()

    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    L = []
    w = L.append
    w("# RedPen `stress_hard` adversarial report")
    w("")
    w(f"_Generated {ts} • {n} cases • full sequential run {wall:.1f}s_")
    w("")
    w("This suite measures how accurately RedPen parses and attributes reality on the inputs a "
      "heavy daily Claude Code user creates on huge, long-lived, multi-workflow repos. Ground "
      "truth is programmatic: every case records the correct verdict per claim. Divergences are "
      "**genuine RedPen findings**, not suite bugs.")
    w("")
    w("## Headline")
    w("")
    w("| metric | value |")
    w("| --- | --- |")
    w(f"| total cases | **{n}** |")
    w(f"| passed (verdict == ground truth) | **{len(passed)}** ({100*len(passed)//n}%) |")
    w(f"| diverged | {n - len(passed)} |")
    w(f"| **false-FAIL** (true claim / user-or-other-session edit / absence marked FAIL) | **{ff_count}** in {_n_cases(len(ff))} |")
    w(f"| **false-OK** (a genuine lie marked OK) | **{fo_count}** in {_n_cases(len(fo))} |")
    w(f"| **misparse** (claim invented or missed) | **{mp_count}** in {_n_cases(len(mp))} |")
    w(f"| soft mismatches (non-headline) | {sum(len(r.soft) for r in results)} in {_n_cases(len(soft))} |")
    w(f"| build / harness errors | {len(errors)} |")
    w("")
    w("> These three dimensions are independent, not a partition: a phantom FAIL counts as both "
      "misparse and false-FAIL. false-FAIL is driven by ground-truth reality (FAIL is only ever "
      "correct on a genuine contradiction); false-OK requires the claim to actually be a lie.")
    w("")

    w("## Latency — deterministic path (redpen `elapsed_seconds`, contention-free)")
    w("")
    w(f"Across {len(non_deep_elapsed)} non-`--deep` cases (incl. the 10k-file tree, the "
      f"~tens-of-thousands-of-token transcript, and the 300-file sprawl):")
    w("")
    w(f"- p50 **{p50:.3f}s** • p95 **{p95:.3f}s** • p99 **{p99:.3f}s** • max **{mx:.3f}s**")
    w(f"- deterministic path stays sub-second: **{'YES' if p99 < 1.0 else 'NO'}** (p99 < 1.0s)")
    big = [(r.cid, r.elapsed) for r in results
           if by_cid_case(cases, r.cid).tags and "latency" in by_cid_case(cases, r.cid).tags
           and r.elapsed is not None]
    for cid, el in big:
        w(f"  - `{cid}`: {el:.3f}s")
    w("")

    w("## Concurrency soak")
    w("")
    w(f"- distinct temp repos, 16-wide: **{soak['distinct_correct']}/{soak['distinct_total']}** "
      "stayed correct (no cross-contamination)")
    w(f"- same-repo ledger contention ({soak['ledger_procs']} concurrent `redpen check`): "
      f"clean-exit={soak['ledger_clean_exit']}, verdicts-consistent={soak['ledger_consistent']}, "
      f"SQLite integrity=**{soak['ledger_integrity']}**, rows={soak['ledger_rows']}")
    w(f"- same-repo judge-cache contention ({soak['deep_procs']} concurrent `--deep`): "
      f"clean-exit={soak['deep_clean_exit']}, judge_cache.json valid=**{soak['judge_cache_valid']}**")
    w("")

    def section(title, rows, key):
        w(f"## {title}")
        w("")
        if not rows:
            w("_none_")
            w("")
            return
        for r in rows:
            w(_repro_block(by_cid, r.cid))
        w("")

    section(f"false-FAIL findings ({ff_count})", ff, "false_fail")
    section(f"false-OK findings ({fo_count})", fo, "false_ok")
    section(f"misparse findings ({mp_count})", mp, "misparse")
    if soft:
        section(f"soft mismatches ({len(soft)})", soft, "soft")
    if errors:
        section(f"build / harness errors ({len(errors)})", errors, "error")

    w("## Findings grouped by root cause")
    w("")
    for (cat, root), cids in sorted(roots.items()):
        w(f"- **{root}** ({cat}, {_n_cases(len(cids))}): " + ", ".join(f"`{c.split('/')[-1]}`" for c in cids))
    w("")

    w("## Per-axis breakdown")
    w("")
    w("| axis | cases | passed |")
    w("| --- | --- | --- |")
    for a in sorted(axes):
        total, p = axes[a]
        w(f"| {a} | {total} | {p} |")
    w("")

    w("## How to reproduce")
    w("")
    w("```sh")
    w("# regenerate this report")
    w(".venv/bin/python tests/stress_hard/run_report.py")
    w("# inspect one case in detail")
    w(".venv/bin/python tests/stress_hard/run_report.py contradiction/zero_failed_summary")
    w("# run the whole suite under pytest-xdist (concurrency sweep)")
    w("REDPEN_STRESS_HARD=1 .venv/bin/python -m pytest tests/stress_hard -n auto")
    w("# the tiny real-claude --deep wiring check (off by default)")
    w("REDPEN_STRESS_LIVE=1 REDPEN_STRESS_HARD=1 .venv/bin/python -m pytest tests/stress_hard/test_live.py")
    w("```")
    w("")

    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {REPORT}")
    print(f"cases={n} passed={len(passed)} false_FAIL={ff_count} false_OK={fo_count} "
          f"misparse={mp_count} soft={sum(len(r.soft) for r in results)} errors={len(errors)}")
    print(f"latency p50={p50:.3f} p95={p95:.3f} p99={p99:.3f} max={mx:.3f}")
    return 0


_CASE_INDEX = None


def by_cid_case(cases, cid):
    global _CASE_INDEX
    if _CASE_INDEX is None:
        _CASE_INDEX = {c.cid: c for c in cases}
    return _CASE_INDEX[cid]


if __name__ == "__main__":
    if len(sys.argv) > 1:
        raise SystemExit(debug_one(sys.argv[1]))
    raise SystemExit(main())

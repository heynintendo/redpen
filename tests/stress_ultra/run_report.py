"""Generate tests/stress_ultra/last_report.md.

Runs the deterministic verdict cases (dimensions A/B/D) contention-free, tallies
the three headline numbers (false-FAIL / false-OK / misparse) with a one-line
repro for any nonzero count, breaks results down per dimension, runs the
concurrency soak (dimension C), and reports latency percentiles.

    .venv/bin/python tests/stress_ultra/run_report.py            # full report
    .venv/bin/python tests/stress_ultra/run_report.py <case-id>  # one case, verbose
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ucases.registry import all_cases  # noqa: E402
from uharness.builders import TB, commit_all, make_repo, write_file  # noqa: E402
from uharness.fake_bins import controlled_path, make_bin_dir  # noqa: E402
from uharness.run_all import run_all  # noqa: E402
from uharness.runner import run_case, run_redpen  # noqa: E402

HERE = Path(__file__).resolve().parent
REPORT = HERE / "last_report.md"

_DIM_NAME = {
    "A": "A · false-OK (the silent betrayal)",
    "B": "B · claim misparse at scale / noise",
    "D": "D · attribution & contradiction at the seams",
}


def _pct(values, p):
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


def debug_one(cid):
    cases = {c.cid: c for c in all_cases()}
    if cid not in cases:
        print(f"no such case: {cid}")
        return 2
    res = run_case(cases[cid], Path(tempfile.mkdtemp(prefix="redpen_ultra_")))
    print(f"case:    {cid}\ntitle:   {cases[cid].title}")
    print(f"passed:  {res.passed}  exit={res.exit_code}  elapsed={res.elapsed}s")
    if res.error:
        print(f"error:   {res.error}")
    for k in ("false_fail", "false_ok", "misparse", "soft"):
        for m in getattr(res, k):
            print(f"  {k}: {m}")
    print("  actual findings:")
    for f in res.actual:
        print(f"    {f['probe']:18} {f['verdict']:13} {f['subject']!r}")
    return 0


# --- dimension C: concurrency soak -------------------------------------------
def _shared_repo(base, name):
    root = make_repo(base / name, {"README.md": "# x\n", ".gitignore": ".redpen/\n"})
    write_file(root, "app.py", "def app():\n    return 1\n")
    commit_all(root, "app")
    t = TB(cwd=root)
    t.user("create app.py and wrap up")
    t.write("app.py")
    t.assistant("Created app.py. Done.")
    return root, t.write_to(base / (name + ".jsonl"))


def _vset(o):
    return tuple(sorted((f["probe"], f["verdict"]) for f in o["data"]["findings"]))


def run_soak():
    out = {"scenarios": 0, "problems": []}
    base = Path(tempfile.mkdtemp(prefix="redpen_ultra_soak_"))

    # (a) distinct repos, 16-wide: each independent case must stay correct.
    cases = [c for c in all_cases() if not c.deep][:60]
    res = run_all(cases, jobs=16)
    out["distinct_total"] = len(res)
    out["distinct_correct"] = sum(1 for r in res if r.passed)
    out["scenarios"] += len(res)
    if out["distinct_correct"] != out["distinct_total"]:
        out["problems"].append("cross-repo contamination: a distinct-repo case diverged 16-wide")

    # (b) same-repo check contention.
    root, tp = _shared_repo(base, "contend")
    home = base / "_h"
    procs = 40
    with ThreadPoolExecutor(max_workers=16) as ex:
        outs = list(ex.map(lambda _: run_redpen(root, transcript=tp, home=home), range(procs)))
    out["scenarios"] += procs
    out["check_clean_exit"] = all(o["rc"] in (0, 1) and not o["timeout"] and o["data"] for o in outs)
    out["check_consistent"] = len({_vset(o) for o in outs}) == 1
    db = root / ".redpen" / "ledger.db"
    conn = sqlite3.connect(str(db))
    try:
        out["ledger_integrity"] = conn.execute("PRAGMA integrity_check").fetchone()[0]
        out["ledger_rows"] = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    finally:
        conn.close()
    if not out["check_clean_exit"]:
        out["problems"].append("same-repo contention: a check crashed/hung")
    if not out["check_consistent"]:
        out["problems"].append("same-repo contention: verdicts varied for identical input")
    if out["ledger_integrity"] != "ok":
        out["problems"].append(f"ledger integrity={out['ledger_integrity']}")

    # (c) --deep judge-cache thundering herd on identical evidence.
    droot = make_repo(base / "deep", {"README.md": "# x\n", ".gitignore": ".redpen/\n"})
    bind = make_bin_dir(base, claude=True)
    dt = TB(cwd=droot)
    dt.user("refactor the parser")
    dt.assistant("Refactored the parser internals.")
    dtp = dt.write_to(base / "dt.jsonl")
    env = {"PATH": controlled_path(bind), "REDPEN_LLM_MODEL": "mock",
           "REDPEN_FAKE_VERDICT": "OK", "REDPEN_FAKE_AUDIT": "[]"}
    dprocs = 20
    with ThreadPoolExecutor(max_workers=12) as ex:
        douts = list(ex.map(
            lambda _: run_redpen(droot, transcript=dtp, extra_args=("--deep",), env_extra=env, home=base / "_dh"),
            range(dprocs)))
    out["scenarios"] += dprocs
    out["deep_clean_exit"] = all(o["rc"] in (0, 1) and not o["timeout"] and o["data"] for o in douts)
    out["deep_consistent"] = len({_vset(o) for o in douts}) == 1
    cache = droot / ".redpen" / "judge_cache.json"
    try:
        json.loads(cache.read_text())
        out["judge_cache_valid"] = True
    except Exception as exc:  # noqa: BLE001
        out["judge_cache_valid"] = False
        out["problems"].append(f"judge_cache corrupt: {exc}")
    if not out["deep_clean_exit"]:
        out["problems"].append("--deep herd: a check crashed/hung")
    if not out["deep_consistent"]:
        out["problems"].append("--deep herd: identical evidence gave different verdicts")

    # (d) recovery from corrupt partial state + read-only .redpen.
    rroot, rtp = _shared_repo(base, "recover")
    rp = rroot / ".redpen"
    rp.mkdir(parents=True, exist_ok=True)
    (rp / "ledger.db").write_bytes(b"not a db")
    (rp / "last_run.json").write_text("{ truncated")
    (rp / "baseline.json").write_text("{ broken")
    rout = run_redpen(rroot, transcript=rtp, home=base / "_rh")
    out["scenarios"] += 1
    out["recovers_from_corrupt"] = (rout["rc"] in (0, 1) and not rout["timeout"] and rout["data"] is not None)
    if not out["recovers_from_corrupt"]:
        out["problems"].append("did not recover from corrupt partial state")
    return out


def main():
    cases = all_cases()
    by_cid = {c.cid: c for c in cases}
    started = time.perf_counter()
    results = run_all(cases, jobs=1)  # contention-free for accurate latency
    wall = time.perf_counter() - started

    ff = [r for r in results if r.false_fail]
    fo = [r for r in results if r.false_ok]
    mp = [r for r in results if r.misparse]
    ff_n = sum(len(r.false_fail) for r in results)
    fo_n = sum(len(r.false_ok) for r in results)
    mp_n = sum(len(r.misparse) for r in results)
    errors = [r for r in results if r.error]

    elapsed = [r.elapsed for r in results if r.elapsed is not None and not by_cid[r.cid].deep]
    p50, p95, p99, mx = _pct(elapsed, 50), _pct(elapsed, 95), _pct(elapsed, 99), (max(elapsed) if elapsed else 0)

    dims = {}
    for r in results:
        d = by_cid[r.cid].axis
        dims.setdefault(d, {"n": 0, "passed": 0, "ff": 0, "fo": 0, "mp": 0})
        dims[d]["n"] += 1
        dims[d]["passed"] += 1 if r.passed else 0
        dims[d]["ff"] += len(r.false_fail)
        dims[d]["fo"] += len(r.false_ok)
        dims[d]["mp"] += len(r.misparse)

    soak = run_soak()
    total_cases = len(results) + soak["scenarios"]

    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    L = []
    w = L.append
    w("# RedPen `stress_ultra` report")
    w("")
    w(f"_Generated {ts} • {len(results)} verdict cases + {soak['scenarios']} concurrency "
      f"scenario-runs = {total_cases} total • sequential verdict pass {wall:.1f}s_")
    w("")
    w("The one-in-a-million failures that burn a corporate user. Ground truth is "
      "unarguable; where a real input is inherently ambiguous the asserted verdict is "
      "the fail-safe UNVERIFIABLE. Spread evenly across four dimensions.")
    w("")
    w("## Headline")
    w("")
    w("| metric | value |")
    w("| --- | --- |")
    w(f"| total cases (verdict + concurrency) | **{total_cases}** |")
    w(f"| verdict cases passed | **{sum(1 for r in results if r.passed)} / {len(results)}** |")
    w(f"| **false-FAIL** (true/ambiguous claim marked FAIL) | **{ff_n}** in {len(ff)} cases |")
    w(f"| **false-OK** (a genuine lie marked OK) | **{fo_n}** in {len(fo)} cases |")
    w(f"| **misparse** (claim invented or missed) | **{mp_n}** in {len(mp)} cases |")
    w(f"| build / harness errors | {len(errors)} |")
    w("")
    w("## Per-dimension breakdown")
    w("")
    w("| dimension | cases | passed | false-FAIL | false-OK | misparse |")
    w("| --- | --- | --- | --- | --- | --- |")
    for d in sorted(dims):
        s = dims[d]
        w(f"| {_DIM_NAME.get(d, d)} | {s['n']} | {s['passed']} | {s['ff']} | {s['fo']} | {s['mp']} |")
    # Dimension C is concurrency: report its scenario count + outcome separately.
    c_ok = not soak["problems"]
    w(f"| C · concurrency / state corruption | {soak['scenarios']} | "
      f"{'all clean' if c_ok else 'PROBLEMS'} | — | — | — |")
    w("")
    w("## Latency — deterministic path (redpen `elapsed_seconds`, contention-free)")
    w("")
    w(f"Across {len(elapsed)} non-`--deep` verdict cases (incl. the ~hundreds-of-thousands-of-token "
      "transcripts):")
    w("")
    w(f"- p50 **{p50:.3f}s** • p95 **{p95:.3f}s** • p99 **{p99:.3f}s** • max **{mx:.3f}s**")
    w(f"- sub-second p99: **{'YES' if p99 < 1.0 else 'NO'}**")
    w("")
    w("## Concurrency soak (dimension C)")
    w("")
    w(f"- distinct repos 16-wide: **{soak['distinct_correct']}/{soak['distinct_total']}** stayed correct (no contamination)")
    w(f"- same-repo check contention (40 procs): clean-exit={soak['check_clean_exit']}, "
      f"verdicts-consistent={soak['check_consistent']}, SQLite integrity=**{soak['ledger_integrity']}**, rows={soak['ledger_rows']}")
    w(f"- `--deep` judge-cache thundering herd (20 procs, identical evidence): clean-exit={soak['deep_clean_exit']}, "
      f"consistent={soak['deep_consistent']}, cache valid=**{soak['judge_cache_valid']}**")
    w(f"- recovery from corrupt partial state: **{soak['recovers_from_corrupt']}**")
    w(f"- result: **{'clean — no corruption, no crashes, no contamination' if c_ok else 'PROBLEMS: ' + '; '.join(soak['problems'])}**")
    w("")

    def section(title, rows, key):
        w(f"## {title}")
        w("")
        if not rows:
            w("_none_")
            w("")
            return
        for r in rows:
            for m in getattr(r, key):
                w(f"- **`{r.cid}`** — {m}")
            actual = ", ".join(f"{f['probe']}={f['verdict']}" for f in r.actual) or "(no findings)"
            w(f"  - redpen returned: {actual}")
            w(f"  - repro: `.venv/bin/python tests/stress_ultra/run_report.py {r.cid}`")
        w("")

    section(f"false-FAIL findings ({ff_n})", ff, "false_fail")
    section(f"false-OK findings ({fo_n})", fo, "false_ok")
    if errors:
        w("## build / harness errors")
        w("")
        for r in errors:
            w(f"- `{r.cid}`: {r.error}")
        w("")

    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {REPORT}")
    print(f"verdict_cases={len(results)} concurrency_scenarios={soak['scenarios']} total={total_cases}")
    print(f"false_FAIL={ff_n} false_OK={fo_n} misparse={mp_n} errors={len(errors)} "
          f"soak_problems={len(soak['problems'])}")
    print(f"latency p50={p50:.3f} p95={p95:.3f} p99={p99:.3f} max={mx:.3f}")
    return 1 if (ff_n or fo_n or errors or soak["problems"]) else 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        raise SystemExit(debug_one(sys.argv[1]))
    raise SystemExit(main())

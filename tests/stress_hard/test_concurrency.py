"""Heavy concurrency soak: no cross-contamination across distinct repos, and no
ledger / judge-cache / state corruption when many redpen processes hit ONE repo.
"""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from cases.registry import all_cases
from harness.builders import TB, commit_all, make_repo, write_file
from harness.fake_bins import controlled_path, make_bin_dir
from harness.run_all import run_all
from harness.runner import run_redpen
from known_findings import KNOWN_DIVERGENCES

pytestmark = pytest.mark.slow


def test_distinct_repo_concurrency_no_contamination():
    """~90 distinct, known-correct cases run 16-wide; each must stay correct.

    Cross-contamination (one repo's state bleeding into another) would show up as
    a wrong verdict here, since every case has independent ground truth.
    """
    cases = [c for c in all_cases()
             if c.cid not in KNOWN_DIVERGENCES and not c.deep
             and c.axis in ("generated", "environment", "attribution", "scale_noise")][:90]
    results = run_all(cases, jobs=16)
    bad = [(r.cid, r.error or (r.false_fail + r.false_ok + r.misparse + r.soft)) for r in results if not r.passed]
    assert not bad, f"{len(bad)} cases diverged under 16-wide concurrency (contamination?): {bad[:6]}"


def _shared_repo(tmp_path):
    root = make_repo(tmp_path / "shared", {"README.md": "# x\n"})
    write_file(root, "app.py", "def app():\n    return 1\n")
    commit_all(root, "app")
    t = TB(cwd=root)
    t.user("create app.py and wrap up")
    t.write("app.py")
    t.assistant("Created app.py. Done.")
    return root, t.write_to(tmp_path / "t.jsonl")


def test_same_repo_ledger_contention(tmp_path):
    """16 concurrent `redpen check` on one repo: no crash, consistent verdicts,
    intact SQLite ledger, valid last_run.json."""
    root, tp = _shared_repo(tmp_path)
    home = tmp_path / "_home"
    procs = 16
    with ThreadPoolExecutor(max_workers=procs) as ex:
        outs = list(ex.map(lambda _: run_redpen(root, transcript=tp, home=home), range(procs)))

    assert all(not o["timeout"] for o in outs), "a concurrent check hung"
    assert all(o["rc"] in (0, 1) for o in outs), [o["rc"] for o in outs]
    assert all(o["data"] is not None for o in outs), "a concurrent check produced no JSON"
    verdict_sets = {
        tuple(sorted((f["probe"], f["verdict"]) for f in o["data"]["findings"]))
        for o in outs
    }
    assert len(verdict_sets) == 1, f"verdicts varied under concurrency: {verdict_sets}"

    db = root / ".redpen" / "ledger.db"
    assert db.exists(), "no ledger written"
    conn = sqlite3.connect(str(db))
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "ledger corrupted"
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] > 0, "no ledger rows"
    finally:
        conn.close()

    last_run = root / ".redpen" / "last_run.json"
    assert json.loads(last_run.read_text())["findings"], "last_run.json missing/invalid"


def test_same_repo_judge_cache_contention(tmp_path):
    """12 concurrent `redpen check --deep` on one repo (fake claude): the shared
    judge_cache.json must remain valid JSON (no corruption from racing writers)."""
    root = make_repo(tmp_path / "shared", {"README.md": "# x\n"})
    bind = make_bin_dir(tmp_path, claude=True)
    t = TB(cwd=root)
    t.user("refactor the parser")
    t.assistant("Refactored the parser internals.")
    tp = t.write_to(tmp_path / "t.jsonl")
    home = tmp_path / "_home"
    env = {"PATH": controlled_path(bind), "REDPEN_LLM_MODEL": "mock",
           "REDPEN_FAKE_VERDICT": "OK", "REDPEN_FAKE_AUDIT": "[]"}
    procs = 12
    with ThreadPoolExecutor(max_workers=procs) as ex:
        outs = list(ex.map(
            lambda _: run_redpen(root, transcript=tp, extra_args=("--deep",), env_extra=env, home=home),
            range(procs)))

    assert all(not o["timeout"] for o in outs), "a concurrent --deep check hung"
    assert all(o["rc"] in (0, 1) for o in outs), [o["rc"] for o in outs]
    assert all(o["data"] is not None for o in outs), "a concurrent --deep check produced no JSON"

    cache = root / ".redpen" / "judge_cache.json"
    assert cache.exists(), "no judge cache written"
    json.loads(cache.read_text())  # raises if a racing writer corrupted it

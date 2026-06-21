"""Dimension C -- concurrency / state corruption under hostile load.

Pushes well past the hard suite: dozens of `redpen check` and `--deep` racing on
the SAME repo and .redpen/ state; ledger-write interleaving; judge-cache
thundering-herd on identical evidence; a check racing a baseline mid-write;
recovery from partial/corrupt state; and a read-only .redpen/. Asserts: no
SQLite corruption, no cross-project contamination, no crash, no hang, and
verdicts stay consistent for identical inputs.

    REDPEN_STRESS_ULTRA=1 .venv/bin/python -m pytest tests/stress_ultra/test_concurrency.py -n auto
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from uharness.builders import TB, commit_all, make_repo, write_file
from uharness.fake_bins import controlled_path, make_bin_dir
from uharness.runner import REPO_ROOT, base_env, run_redpen

pytestmark = pytest.mark.slow


def _shared_repo(tmp_path, name="shared"):
    root = make_repo(tmp_path / name, {"README.md": "# x\n", ".gitignore": ".redpen/\n"})
    write_file(root, "app.py", "def app():\n    return 1\n")
    commit_all(root, "app")
    t = TB(cwd=root)
    t.user("create app.py and wrap up")
    t.write("app.py")
    t.assistant("Created app.py. Done.")
    return root, t.write_to(tmp_path / "t.jsonl")


def _verdict_set(out):
    return tuple(sorted((f["probe"], f["verdict"]) for f in out["data"]["findings"]))


# --- 1) heavy same-repo check contention -------------------------------------


@pytest.mark.parametrize("procs", [16, 32, 48])
def test_same_repo_check_contention(tmp_path, procs):
    root, tp = _shared_repo(tmp_path)
    home = tmp_path / "_home"
    with ThreadPoolExecutor(max_workers=procs) as ex:
        outs = list(ex.map(lambda _: run_redpen(root, transcript=tp, home=home), range(procs)))

    assert all(not o["timeout"] for o in outs), "a concurrent check hung"
    assert all(o["rc"] in (0, 1) for o in outs), [o["rc"] for o in outs]
    assert all(o["data"] is not None for o in outs), "a concurrent check produced no JSON"
    assert len({_verdict_set(o) for o in outs}) == 1, "verdicts varied under concurrency"

    db = root / ".redpen" / "ledger.db"
    conn = sqlite3.connect(str(db))
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "ledger corrupted"
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] > 0
    finally:
        conn.close()
    json.loads((root / ".redpen" / "last_run.json").read_text())  # raises if corrupt


# --- 2) check + --deep racing on the same repo (mixed load) ------------------


def test_mixed_check_and_deep_race(tmp_path):
    root, tp = _shared_repo(tmp_path)
    bind = make_bin_dir(tmp_path, claude=True)
    home = tmp_path / "_home"
    deep_env = {"PATH": controlled_path(bind), "REDPEN_LLM_MODEL": "mock",
                "REDPEN_FAKE_VERDICT": "OK", "REDPEN_FAKE_AUDIT": "[]"}

    def one(i):
        if i % 2 == 0:
            return run_redpen(root, transcript=tp, home=home)
        return run_redpen(root, transcript=tp, extra_args=("--deep",), env_extra=deep_env, home=home)

    with ThreadPoolExecutor(max_workers=24) as ex:
        outs = list(ex.map(one, range(24)))
    assert all(not o["timeout"] and o["rc"] in (0, 1) and o["data"] is not None for o in outs)
    cache = root / ".redpen" / "judge_cache.json"
    if cache.exists():
        json.loads(cache.read_text())  # racing writers must not corrupt it
    db = root / ".redpen" / "ledger.db"
    conn = sqlite3.connect(str(db))
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


# --- 3) judge-cache thundering herd on IDENTICAL evidence --------------------


def test_judge_cache_thundering_herd(tmp_path):
    root = make_repo(tmp_path / "deep", {"README.md": "# x\n", ".gitignore": ".redpen/\n"})
    bind = make_bin_dir(tmp_path, claude=True)
    t = TB(cwd=root)
    t.user("refactor the parser")
    t.assistant("Refactored the parser internals.")  # one UNVERIFIABLE claim -> one judge call
    tp = t.write_to(tmp_path / "t.jsonl")
    home = tmp_path / "_home"
    env = {"PATH": controlled_path(bind), "REDPEN_LLM_MODEL": "mock",
           "REDPEN_FAKE_VERDICT": "OK", "REDPEN_FAKE_AUDIT": "[]"}
    procs = 24
    with ThreadPoolExecutor(max_workers=procs) as ex:
        outs = list(ex.map(
            lambda _: run_redpen(root, transcript=tp, extra_args=("--deep",), env_extra=env, home=home),
            range(procs)))
    assert all(not o["timeout"] and o["rc"] in (0, 1) and o["data"] is not None for o in outs)
    assert len({_verdict_set(o) for o in outs}) == 1, "identical evidence yielded different verdicts"
    json.loads((root / ".redpen" / "judge_cache.json").read_text())


# --- 4) a check racing a `baseline` mid-write --------------------------------


def _run_baseline(root, home):
    env = base_env(Path(home))
    return subprocess.run([sys.executable, "-m", "redpen.cli", "baseline", "--quiet"],
                          cwd=str(root), env=env, capture_output=True, text=True, timeout=60)


def test_check_racing_baseline_write(tmp_path):
    root, tp = _shared_repo(tmp_path)
    home = tmp_path / "_home"

    def task(i):
        if i % 5 == 0:
            _run_baseline(root, home)
            return None
        return run_redpen(root, transcript=tp, home=home)

    with ThreadPoolExecutor(max_workers=16) as ex:
        outs = [o for o in ex.map(task, range(40)) if o is not None]
    assert all(not o["timeout"] and o["rc"] in (0, 1) and o["data"] is not None for o in outs)
    base = root / ".redpen" / "baseline.json"
    if base.exists():
        json.loads(base.read_text())  # a half-written baseline must not be left corrupt


# --- 5) recovery from partial / corrupt state --------------------------------


def test_recovery_from_corrupt_partial_state(tmp_path):
    root, tp = _shared_repo(tmp_path)
    home = tmp_path / "_home"
    rp = root / ".redpen"
    rp.mkdir(parents=True, exist_ok=True)
    (rp / "ledger.db").write_bytes(b"SQLite format 3\x00 garbage not a real db")
    (rp / "last_run.json").write_text('{"findings": [ truncated')
    (rp / "baseline.json").write_text("{ not json at all ")
    (rp / "judge_cache.json").write_text("}{ broken")

    out = run_redpen(root, transcript=tp, home=home)
    assert not out["timeout"], "check hung on corrupt pre-existing state"
    assert out["rc"] in (0, 1), f"check crashed on corrupt state: rc={out['rc']} {out['stderr'][:200]}"
    assert out["data"] is not None, f"no JSON on corrupt state: {out['stderr'][:200]}"


# --- 6) read-only .redpen/: best-effort writes must never crash the check -----


def test_readonly_redpen_dir_does_not_crash(tmp_path):
    root, tp = _shared_repo(tmp_path)
    home = tmp_path / "_home"
    run_redpen(root, transcript=tp, home=home)  # create .redpen first
    rp = root / ".redpen"
    mode = rp.stat().st_mode
    os.chmod(rp, 0o500)  # read+execute only -> writes fail
    try:
        out = run_redpen(root, transcript=tp, home=home)
        assert not out["timeout"]
        assert out["rc"] in (0, 1), f"check crashed when .redpen was read-only: {out['stderr'][:200]}"
        assert out["data"] is not None
    finally:
        os.chmod(rp, mode)

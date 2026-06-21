"""Tiny --live subset: exercise the REAL headless `claude -p` wiring once, to
confirm the --deep plumbing works end-to-end against the actual CLI.

Off by default. The verdict is not asserted (the real model is nondeterministic);
only that the call completes and returns well-formed JSON. Enable with:

    REDPEN_STRESS_LIVE=1 REDPEN_STRESS_HARD=1 .venv/bin/python -m pytest tests/stress_hard/test_live.py
"""

from __future__ import annotations

import os
import shutil

import pytest

from harness.builders import TB, make_repo
from harness.runner import run_redpen

pytestmark = pytest.mark.live

_ENABLED = os.environ.get("REDPEN_STRESS_LIVE") == "1" and shutil.which("claude") is not None


@pytest.mark.skipif(not _ENABLED, reason="set REDPEN_STRESS_LIVE=1 with claude logged in")
def test_live_deep_smoke(tmp_path):
    root = make_repo(tmp_path / "repo", {"README.md": "# x\n"})
    t = TB(cwd=root)
    t.user("refactor the parser")
    t.assistant("Refactored the parser internals.")
    tp = t.write_to(tmp_path / "t.jsonl")
    # Inherit the real PATH so the genuine `claude` binary is used.
    out = run_redpen(root, transcript=tp, extra_args=("--deep",),
                     env_extra={"PATH": os.environ["PATH"]}, home=tmp_path / "_home", timeout=120)
    assert not out["timeout"], "real `claude -p --deep` hung"
    assert out["rc"] in (0, 1), f"unexpected exit {out['rc']}: {out['stderr'][:300]}"
    assert out["data"] is not None, f"no JSON from real --deep run: {out['stderr'][:300]}"
    assert "findings" in out["data"]

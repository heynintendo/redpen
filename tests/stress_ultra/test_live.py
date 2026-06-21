"""Tiny off-by-default --live wiring check against the REAL `claude` CLI.

The verdict is not asserted (the real model is nondeterministic); only that the
--deep plumbing completes and returns well-formed JSON. Enable with:

    REDPEN_STRESS_LIVE=1 REDPEN_STRESS_ULTRA=1 .venv/bin/python -m pytest tests/stress_ultra/test_live.py
"""

from __future__ import annotations

import os
import shutil

import pytest

from uharness.builders import TB, make_repo
from uharness.runner import run_redpen

pytestmark = pytest.mark.live

_ENABLED = os.environ.get("REDPEN_STRESS_LIVE") == "1" and shutil.which("claude") is not None


@pytest.mark.skipif(not _ENABLED, reason="set REDPEN_STRESS_LIVE=1 with claude logged in")
def test_live_deep_smoke(tmp_path):
    root = make_repo(tmp_path / "repo", {"README.md": "# x\n"})
    t = TB(cwd=root)
    t.user("refactor the parser")
    t.assistant("Refactored the parser internals.")
    tp = t.write_to(tmp_path / "t.jsonl")
    out = run_redpen(root, transcript=tp, extra_args=("--deep",),
                     env_extra={"PATH": os.environ["PATH"]}, home=tmp_path / "_home", timeout=120)
    assert not out["timeout"] and out["rc"] in (0, 1) and out["data"] is not None
    assert "findings" in out["data"]

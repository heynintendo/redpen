"""Local pytest config for the stress_hard suite.

Isolated from the rest of tests/: it only puts this directory on sys.path (so
``harness`` / ``cases`` import as top-level packages) and gates collection behind
REDPEN_STRESS_HARD=1 so a plain ``pytest`` run elsewhere never triggers this
heavy, subprocess-spawning suite. Run it with:

    REDPEN_STRESS_HARD=1 .venv/bin/python -m pytest tests/stress_hard -n auto
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: heavy soak / latency tests")
    config.addinivalue_line("markers", "live: exercises the real `claude` CLI")


def pytest_collection_modifyitems(config, items):
    if os.environ.get("REDPEN_STRESS_HARD") == "1":
        return
    skip = pytest.mark.skip(reason="set REDPEN_STRESS_HARD=1 to run the stress_hard suite")
    here = os.path.dirname(__file__)
    for item in items:
        if str(item.fspath).startswith(here):
            item.add_marker(skip)

"""Local pytest config for the stress_ultra suite.

Fully isolated: it only puts THIS directory on sys.path (so ``uharness`` /
``ucases`` import as top-level packages, distinct from the stress_hard suite's
``harness`` / ``cases``), and gates collection behind REDPEN_STRESS_ULTRA=1 so a
plain ``pytest`` run never triggers this heavy, subprocess-spawning suite.

    REDPEN_STRESS_ULTRA=1 .venv/bin/python -m pytest tests/stress_ultra -n auto
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
    if os.environ.get("REDPEN_STRESS_ULTRA") == "1":
        return
    skip = pytest.mark.skip(reason="set REDPEN_STRESS_ULTRA=1 to run the stress_ultra suite")
    here = os.path.dirname(__file__)
    for item in items:
        if str(item.fspath).startswith(here):
            item.add_marker(skip)

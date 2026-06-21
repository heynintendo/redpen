"""Aggregate every case module into one deterministically-ordered list.

Modules are picked up if their file exists, so the suite can be built and run
incrementally. Duplicate case ids are a hard error (they would corrupt the
report and the per-case seeds).
"""

from __future__ import annotations

import importlib
import os

_MODULE_ORDER = [
    "claim_extraction",
    "attribution",
    "contradiction",
    "scale_noise",
    "deep_decomp",
    "environment",
    "generated",
]

_HERE = os.path.dirname(__file__)


def all_cases():
    cases = []
    seen = set()
    for name in _MODULE_ORDER:
        if not os.path.exists(os.path.join(_HERE, name + ".py")):
            continue
        mod = importlib.import_module(f"cases.{name}")
        for case in mod.cases():
            key = case.cid.lower()  # case-insensitive: workspace dirs must not collide
            if key in seen:
                raise ValueError(f"duplicate case id (case-insensitive): {case.cid}")
            seen.add(key)
            cases.append(case)
    return cases


def cases_by_axis():
    by = {}
    for c in all_cases():
        by.setdefault(c.axis, []).append(c)
    return by

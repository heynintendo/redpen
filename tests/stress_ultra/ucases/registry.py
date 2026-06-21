"""Aggregate the deterministic, ground-truth ultra cases (dimensions A, B, D).

Dimension C (concurrency / state corruption) is not verdict-per-case; it lives in
test_concurrency.py and run_report.run_soak(). Property-based dimension-B noise
generation lives in test_property_misparse.py.
"""

from __future__ import annotations

import importlib
import os

_MODULE_ORDER = [
    "dim_a_false_ok",
    "dim_b_misparse",
    "dim_d_seams",
]

_HERE = os.path.dirname(__file__)


def all_cases():
    cases = []
    seen = set()
    for name in _MODULE_ORDER:
        if not os.path.exists(os.path.join(_HERE, name + ".py")):
            continue
        mod = importlib.import_module(f"ucases.{name}")
        for case in mod.cases():
            key = case.cid.lower()
            if key in seen:
                raise ValueError(f"duplicate case id (case-insensitive): {case.cid}")
            seen.add(key)
            cases.append(case)
    return cases


def cases_by_dimension():
    """Group by the leading path segment of the cid (A/B/D)."""
    by = {}
    for c in all_cases():
        dim = c.axis
        by.setdefault(dim, []).append(c)
    return by

"""Baseline of cases where RedPen currently diverges from ground truth.

These are the genuine findings this suite surfaces -- NOT bugs in the suite. The
pytest layer treats them as expected (a known-finding case must still diverge;
if one starts passing, update this file). The report recomputes everything from
scratch and does not depend on this list.

Each entry: case id -> (category, root-cause tag). Categories are the three the
report headlines: false_FAIL, false_OK, misparse.
"""

from __future__ import annotations

KNOWN_DIVERGENCES = {
    # The six root causes that produced 15 false-FAILs and 1 false-OK are fixed
    # (redpen/contradiction.py, git_probes.git_clean, file_probes.file_present,
    # patterns.py, claims.py). What remains is a single fail-safe miss:
    #
    # split_chitchat_final -- a real claim lives only in a non-final assistant
    # turn while the final message is pure chit-chat. RedPen scopes to the final
    # turn and MISSES the claim (emits no finding). This fails safe: a miss, never
    # a confident wrong verdict. Eagerly looking back would resurrect a creation
    # claim for an absent file and emit a confident FAIL -- trading a safe miss
    # for a false-FAIL -- so it's kept as a known miss instead.
    "claim_extraction/split_chitchat_final": ("misparse", "final-message-only-scoping"),
}

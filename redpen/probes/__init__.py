"""RedPen probe library.

Each probe is a small function ``probe(ctx, **kwargs) -> ProbeResult``. The
PROBES registry maps probe names (as referenced by the claim extractor) to
their callables.
"""

from __future__ import annotations

from .base import ProbeContext, ProbeResult, ProbeSpec, Verdict
from .custom_probe import custom_rule
from .file_probes import file_present, todos_remaining
from .gh_probes import pr_status
from .git_probes import branch_synced, git_clean, git_pushed
from .meta_probes import unmapped
from .pack_probes import dep_present, symbol_exists, test_count, typecheck_clean
from .run_probes import build_ok, lint_clean, tests_pass
from .transcript_probes import contradiction_scan, exit_code_scan

PROBES: dict = {
    "file_present": file_present,
    "todos_remaining": todos_remaining,
    "git_pushed": git_pushed,
    "git_clean": git_clean,
    "branch_synced": branch_synced,
    "contradiction_scan": contradiction_scan,
    "exit_code_scan": exit_code_scan,  # back-compat alias
    "tests_pass": tests_pass,
    "build_ok": build_ok,
    "lint_clean": lint_clean,
    "pr_status": pr_status,
    "dep_present": dep_present,
    "typecheck_clean": typecheck_clean,
    "test_count": test_count,
    "symbol_exists": symbol_exists,
    "custom_rule": custom_rule,
    "unmapped": unmapped,
}

__all__ = [
    "ProbeContext",
    "ProbeResult",
    "ProbeSpec",
    "Verdict",
    "PROBES",
    "file_present",
    "todos_remaining",
    "git_pushed",
    "git_clean",
    "branch_synced",
    "contradiction_scan",
    "exit_code_scan",
    "tests_pass",
    "build_ok",
    "lint_clean",
    "pr_status",
    "dep_present",
    "typecheck_clean",
    "test_count",
    "symbol_exists",
    "custom_rule",
    "unmapped",
]

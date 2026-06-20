"""RedPen probe library.

Each probe is a small function ``probe(ctx, **kwargs) -> ProbeResult``. The
PROBES registry maps probe names (as referenced by the claim extractor) to
their callables.
"""

from __future__ import annotations

from .base import ProbeContext, ProbeResult, ProbeSpec, Verdict
from .file_probes import file_present, todos_remaining
from .gh_probes import pr_status
from .git_probes import branch_synced, git_clean, git_pushed
from .meta_probes import unmapped
from .run_probes import build_ok, lint_clean, tests_pass
from .transcript_probes import exit_code_scan

PROBES: dict = {
    "file_present": file_present,
    "todos_remaining": todos_remaining,
    "git_pushed": git_pushed,
    "git_clean": git_clean,
    "branch_synced": branch_synced,
    "exit_code_scan": exit_code_scan,
    "tests_pass": tests_pass,
    "build_ok": build_ok,
    "lint_clean": lint_clean,
    "pr_status": pr_status,
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
    "exit_code_scan",
    "tests_pass",
    "build_ok",
    "lint_clean",
    "pr_status",
    "unmapped",
]

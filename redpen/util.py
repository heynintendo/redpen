"""Tiny subprocess helper used by the shelling-out probes.

Stdlib only. Returns a uniform ``(returncode, stdout, stderr)`` and never
raises on a failed command -- a non-zero exit is data, not an exception.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .config import PROBE_TIMEOUT_SECONDS

# Sentinel return codes for conditions that aren't a real process exit.
RC_TIMEOUT = 124
RC_NOT_FOUND = 127


def run(
    cmd: list[str],
    cwd: Path | str | None = None,
    timeout: float = PROBE_TIMEOUT_SECONDS,
) -> tuple[int, str, str]:
    """Run ``cmd`` and return (returncode, stdout, stderr).

    Never raises for ordinary failures: a timeout maps to RC_TIMEOUT and a
    missing executable to RC_NOT_FOUND, so callers branch on the code.
    """
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return RC_TIMEOUT, "", f"timed out after {timeout}s"
    except FileNotFoundError:
        return RC_NOT_FOUND, "", f"executable not found: {cmd[0]}"

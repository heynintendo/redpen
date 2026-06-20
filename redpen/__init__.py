"""RedPen -- a fast, brutal verifier for Claude Code's completion claims.

Claude Code says it's done. RedPen extracts each success claim and diffs it
against real system state. Claim vs. reality, three verdicts, no re-exploring.
"""

from __future__ import annotations

from .config import TOOL_NAME

__version__ = "0.1.0"
__all__ = ["TOOL_NAME", "__version__"]

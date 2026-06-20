"""Central configuration for RedPen.

Everything tunable lives here so the tool can be renamed or retargeted in one
place. ``TOOL_NAME`` in particular is referenced everywhere the product name
appears, so a fork only needs to change this single constant.
"""

from __future__ import annotations

# The product name. Referenced everywhere the name is surfaced.
TOOL_NAME = "RedPen"

# Hard speed budget for the deterministic path. The whole probe suite is
# expected to finish under this many seconds with no network calls except the
# explicit git-remote / gh probes (branch_synced, pr_status).
SPEED_BUDGET_SECONDS = 2.0

# Per-subprocess timeout for probes that shell out (git, gh, test runners).
PROBE_TIMEOUT_SECONDS = 15

# Where the per-project ledger is stored, relative to the project root.
LEDGER_DIR = ".redpen"
LEDGER_DB = "ledger.db"

# --- Phase 2: the LLM judge ------------------------------------------------
# The judge sees ONLY the evidence a probe already gathered -- it never reads
# the codebase. It runs on `redpen check --deep` to resolve UNVERIFIABLE claims.
# See redpen/judge.py for the contract and the headless `claude -p` call.

# Master switch for the deep LLM layer. --deep engages the judge only when this
# is True; set False to make --deep degrade to deterministic-only.
ENABLE_LLM = True

# Model the judge invokes via `claude -p --model <LLM_MODEL>`. Default sonnet
# for stronger judgement; switch this one line to "haiku" for faster/cheaper.
LLM_MODEL = "sonnet"

# Hard timeout (seconds) for a single headless `claude -p` judge call.
JUDGE_TIMEOUT_SECONDS = 30

"""Opt-in Claude Code Stop hook: run `redpen check` when a task finishes.

Strictly opt-in (installed only by `redpen install-hook`) and fully reversible
(`redpen uninstall-hook`). Three properties matter:

* Deterministic only. The hook runs plain `redpen check` -- NEVER `--deep`. It
  makes no LLM calls, so there's no surprise quota use or latency.
* No recursion. RedPen's own judge calls already spawn `claude -p` with hooks
  disabled, so they can't fire this hook. As a second guard the command sets
  ``REDPEN_HOOK=1``; with it set, `redpen check` refuses `--deep` outright.
* Reversible and unobtrusive. It edits the personal, git-ignored
  ``.claude/settings.local.json`` (not the shared settings.json), and uninstall
  removes exactly what install added, leaving other settings untouched.
"""

from __future__ import annotations

import json
from pathlib import Path

# Commands Claude Code runs. `|| true` keeps them non-blocking. The Stop hook
# verifies the finished task, then refreshes the baseline for the next one. The
# SessionStart hook snapshots the baseline at task start (when supported) so the
# session changed-set can bound the delta. Both degrade gracefully.
HOOK_COMMAND = "REDPEN_HOOK=1 redpen check --no-art; redpen baseline >/dev/null 2>&1 || true"
BASELINE_COMMAND = "redpen baseline >/dev/null 2>&1 || true"

# The events we install on, and the command for each.
_EVENTS = {"Stop": HOOK_COMMAND, "SessionStart": BASELINE_COMMAND}

# Substrings that identify a hook entry as ours (for idempotent un/install).
_MARKERS = ("redpen check", "redpen baseline")


def settings_path(project_root: Path | str) -> Path:
    return Path(project_root) / ".claude" / "settings.local.json"


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _is_ours(hook: dict) -> bool:
    return isinstance(hook, dict) and any(m in str(hook.get("command", "")) for m in _MARKERS)


def install_hook(project_root: Path | str) -> tuple[bool, str]:
    """Install the Stop + SessionStart hooks. Returns (changed, path-or-reason)."""
    path = settings_path(project_root)
    data = _load(path)
    hooks = data.setdefault("hooks", {})

    changed = False
    for event, command in _EVENTS.items():
        groups = hooks.setdefault(event, [])
        if any(_is_ours(h) for g in groups for h in g.get("hooks", [])):
            continue  # already installed for this event
        groups.append({"hooks": [{"type": "command", "command": command}]})
        changed = True

    if not changed:
        return (False, "already installed")
    _write(path, data)
    return (True, str(path))


def uninstall_hook(project_root: Path | str) -> tuple[bool, str]:
    """Remove only RedPen's hooks, leaving everything else intact."""
    path = settings_path(project_root)
    if not path.exists():
        return (False, "no settings file")

    data = _load(path)
    hooks = data.get("hooks", {})
    removed = False
    for event in list(hooks.keys()):
        new_groups = []
        for group in hooks.get(event, []):
            kept = [h for h in group.get("hooks", []) if not _is_ours(h)]
            if len(kept) != len(group.get("hooks", [])):
                removed = True
            if kept:
                g = dict(group)
                g["hooks"] = kept
                new_groups.append(g)
        if new_groups:
            hooks[event] = new_groups
        else:
            hooks.pop(event, None)

    if not removed:
        return (False, "not installed")
    if not hooks:
        data.pop("hooks", None)
    _write(path, data)
    return (True, str(path))

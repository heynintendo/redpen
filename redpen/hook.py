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

# The command Claude Code runs on Stop. `|| true` keeps it non-blocking (a FAIL
# verdict is printed but never blocks the stop or feeds back to the model).
HOOK_COMMAND = "REDPEN_HOOK=1 redpen check --no-art || true"

# Substring that identifies a hook entry as ours (for idempotent un/install).
_MARKER = "redpen check"


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
    return isinstance(hook, dict) and _MARKER in str(hook.get("command", ""))


def install_hook(project_root: Path | str) -> tuple[bool, str]:
    """Install the Stop hook. Returns (changed, path-or-reason)."""
    path = settings_path(project_root)
    data = _load(path)
    stop = data.setdefault("hooks", {}).setdefault("Stop", [])

    for group in stop:
        if any(_is_ours(h) for h in group.get("hooks", [])):
            return (False, "already installed")

    stop.append({"hooks": [{"type": "command", "command": HOOK_COMMAND}]})
    _write(path, data)
    return (True, str(path))


def uninstall_hook(project_root: Path | str) -> tuple[bool, str]:
    """Remove only RedPen's Stop hook, leaving everything else intact."""
    path = settings_path(project_root)
    if not path.exists():
        return (False, "no settings file")

    data = _load(path)
    stop = data.get("hooks", {}).get("Stop", [])
    new_stop = []
    removed = False
    for group in stop:
        kept = [h for h in group.get("hooks", []) if not _is_ours(h)]
        if len(kept) != len(group.get("hooks", [])):
            removed = True
        if kept:
            g = dict(group)
            g["hooks"] = kept
            new_stop.append(g)

    if not removed:
        return (False, "not installed")

    # Prune now-empty containers so we leave the file as we found it.
    if new_stop:
        data["hooks"]["Stop"] = new_stop
    else:
        data["hooks"].pop("Stop", None)
        if not data["hooks"]:
            data.pop("hooks", None)
    _write(path, data)
    return (True, str(path))

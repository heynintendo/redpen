"""Locate and parse the Claude Code session transcript.

Claude Code writes one JSONL file per session under
``~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`` where the directory
name is the project path with separators replaced by ``-`` (e.g.
``/Users/me/proj`` -> ``-Users-me-proj``).

We parse only what the probes need -- final assistant text, tool events with a
pass/fail flag, and the list of files the assistant touched. We never re-read
the codebase; the transcript is the single source of "what was claimed".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# Tools whose invocation means the assistant wrote to a file.
_FILE_WRITE_TOOLS = {
    "Write": "file_path",
    "Edit": "file_path",
    "MultiEdit": "file_path",
    "NotebookEdit": "notebook_path",
}


@dataclass
class ToolEvent:
    """One tool call and how it turned out."""

    tool: str
    label: str
    failed: bool
    exit_code: int | None = None


@dataclass
class Transcript:
    """The parsed essentials of a session, for the probes to judge against."""

    session_id: str = ""
    path: str | None = None
    cwd: str | None = None
    entrypoint: str = ""
    tool_events: list[ToolEvent] = field(default_factory=list)
    touched_files: list[str] = field(default_factory=list)
    assistant_texts: list[str] = field(default_factory=list)
    final_assistant_text: str = ""
    user_texts: list[str] = field(default_factory=list)
    final_user_text: str = ""


def encode_project_dir(cwd: Path | str) -> str:
    """Encode a project path the way Claude Code names its transcript dir."""
    return str(Path(cwd).resolve()).replace("/", "-")


def _candidate_dirs(cwd: Path | str, base: Path) -> list[Path]:
    resolved = str(Path(cwd).resolve())
    names = {
        resolved.replace("/", "-"),
        re.sub(r"[^A-Za-z0-9]", "-", resolved),  # Claude Code also folds '.', '_'
    }
    return [base / n for n in names]


def transcript_base(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".claude" / "projects"


# Entrypoints that mean "RedPen's own headless `claude -p` call", not a real
# interactive session. Auto-discovery must never surface these -- otherwise
# RedPen would audit its own judge/decompose calls (a recursion footgun).
_HEADLESS_ENTRYPOINTS = {"sdk-cli"}


def _peek_entrypoint(path: Path, max_lines: int = 60) -> str | None:
    """Cheaply read a transcript's entrypoint without a full parse."""
    try:
        with path.open(encoding="utf-8") as fh:
            for _ in range(max_lines):
                raw = fh.readline()
                if not raw:
                    break
                try:
                    ep = json.loads(raw).get("entrypoint")
                except json.JSONDecodeError:
                    continue
                if ep:
                    return ep
    except OSError:
        return None
    return None


def is_headless_transcript(path: Path) -> bool:
    """True if this file is one of RedPen's own headless `claude -p` calls."""
    return _peek_entrypoint(path) in _HEADLESS_ENTRYPOINTS


def latest_transcript_for(cwd: Path | str, home: Path | None = None) -> Path | None:
    """Most recently modified real session ``*.jsonl`` for this project, or None.

    Skips headless `claude -p` transcripts (entrypoint ``sdk-cli``) so RedPen
    never auto-discovers its own judge/decompose calls.
    """
    base = transcript_base(home)
    candidates: list[Path] = []
    for d in _candidate_dirs(cwd, base):
        if d.is_dir():
            candidates.extend(d.glob("*.jsonl"))
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        if not is_headless_transcript(path):
            return path
    return None


def _result_failed(line: dict) -> bool | None:
    """Decide if a transcript line is a *failed* tool result.

    Returns True (failed), False (succeeded), or None (not a tool result).
    Failure signals, in order: an ``is_error`` tool_result block, or a
    top-level ``toolUseResult.success == False``.
    """
    is_result = False
    msg = line.get("message") or {}
    content = msg.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                is_result = True
                if block.get("is_error") is True:
                    return True

    tur = line.get("toolUseResult")
    if isinstance(tur, dict):
        is_result = True
        if tur.get("success") is False:
            return True

    return False if is_result else None


def _result_tool_use_id(line: dict) -> str | None:
    msg = line.get("message") or {}
    content = msg.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                return block.get("tool_use_id")
    return line.get("sourceToolUseID")


def parse_transcript(path: Path | str) -> Transcript:
    """Parse a transcript JSONL into the essentials the probes need."""
    path = Path(path)
    t = Transcript(path=str(path))
    pending: dict[str, tuple[str, str]] = {}  # tool_use_id -> (tool, label)
    seen_touched: set[str] = set()

    with path.open(encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                line = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if not t.session_id and line.get("sessionId"):
                t.session_id = line["sessionId"]
            if not t.cwd and line.get("cwd"):
                t.cwd = line["cwd"]
            if not t.entrypoint and line.get("entrypoint"):
                t.entrypoint = line["entrypoint"]

            ltype = line.get("type")

            if ltype == "assistant":
                msg = line.get("message") or {}
                content = msg.get("content") or []
                texts: list[str] = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "text":
                        texts.append(block.get("text", ""))
                    elif btype == "tool_use":
                        tool = block.get("name", "")
                        tid = block.get("id", "")
                        inp = block.get("input") or {}
                        label = _tool_label(tool, inp)
                        if tid:
                            pending[tid] = (tool, label)
                        key = _FILE_WRITE_TOOLS.get(tool)
                        if key and inp.get(key):
                            fp = str(inp[key])
                            if fp not in seen_touched:
                                seen_touched.add(fp)
                                t.touched_files.append(fp)
                joined = "\n".join(s for s in texts if s).strip()
                if joined:
                    t.assistant_texts.append(joined)
                continue

            if ltype == "user":
                msg = line.get("message") or {}
                content = msg.get("content")
                is_tool_result = isinstance(content, list) and any(
                    isinstance(b, dict) and b.get("type") == "tool_result" for b in content
                )
                # A genuine human prompt, not a tool result or meta/sidechain turn.
                if not is_tool_result and not line.get("isMeta") and not line.get("isSidechain"):
                    text = _user_text(content)
                    if text:
                        t.user_texts.append(text)
                    continue
                # Otherwise fall through: tool-result turns are handled below.

            failed = _result_failed(line)
            if failed is not None:
                tid = _result_tool_use_id(line)
                tool, label = pending.get(tid or "", ("", ""))
                if not tool:
                    tur = line.get("toolUseResult")
                    if isinstance(tur, dict):
                        tool = tur.get("commandName", "") or "tool"
                        label = label or tur.get("commandName", "")
                t.tool_events.append(
                    ToolEvent(tool=tool or "tool", label=label or tool or "tool", failed=failed)
                )

    if t.assistant_texts:
        t.final_assistant_text = t.assistant_texts[-1]
    if t.user_texts:
        t.final_user_text = t.user_texts[-1]
    return t


def _user_text(content) -> str:
    """Extract human-typed text from a user message's content."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p).strip()
    return ""


def _tool_label(tool: str, inp: dict) -> str:
    if tool == "Bash":
        cmd = str(inp.get("command", "")).strip().replace("\n", " ")
        return cmd[:80]
    for key in ("file_path", "notebook_path", "path", "pattern", "url"):
        if inp.get(key):
            return f"{tool}({inp[key]})"
    return tool

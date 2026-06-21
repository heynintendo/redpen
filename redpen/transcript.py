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
import os
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
    """One tool call and how it turned out.

    ``command`` is the full Bash command (or file path); ``output`` is a snippet
    of the captured stdout/stderr -- the raw material the contradiction engine
    scans for failure signatures.
    """

    tool: str
    label: str
    failed: bool
    exit_code: int | None = None
    command: str = ""
    output: str = ""


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


def _mentions_path(path: Path, needle: str) -> bool:
    """True if the transcript's raw text references ``needle`` (a cwd path).

    Claude Code files a transcript under the directory the session LAUNCHED from,
    not the agent's later cwd. So a session started in ~ that worked in
    ~/sorting-algorithms/ is filed under ~ -- but the agent's cwd/file paths inside
    it reference ~/sorting-algorithms/. That reference is how we confirm an
    ancestor-launched session is the relevant one before accepting it.
    """
    try:
        with path.open(encoding="utf-8", errors="ignore") as fh:
            return needle in fh.read()
    except OSError:
        return False


def active_session_id() -> str | None:
    """The id of the Claude Code session RedPen is running inside, if any.

    Claude Code exposes it as ``CLAUDE_CODE_SESSION_ID``; the session's transcript
    file is named ``<id>.jsonl``. This is the authoritative way to grade the
    CURRENT session rather than whichever transcript happens to be newest.
    """
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("CLAUDE_SESSION_ID")
    return sid.strip() if sid and sid.strip() else None


@dataclass
class Discovery:
    """The outcome of locating a session transcript to grade."""

    path: Path | None = None
    source: str = ""          # "session" | "own" | "reference" | ""
    alternatives: int = 0     # other non-headless candidates that were considered

    @property
    def ambiguous(self) -> bool:
        """A heuristic pick made while other candidates also existed -> we can't
        be sure it's the current session."""
        return self.source in ("own", "reference") and self.alternatives > 0


def _candidate_project_dirs(cwd: Path | str, base: Path) -> tuple[list[Path], set[str]]:
    own = {str(d) for d in _candidate_dirs(cwd, base)}
    search = list(_candidate_dirs(cwd, base))
    for ancestor in Path(cwd).resolve().parents:
        search.extend(_candidate_dirs(ancestor, base))
    dirs: list[Path] = []
    seen: set[str] = set()
    for d in search:
        if d.is_dir() and str(d) not in seen:
            seen.add(str(d))
            dirs.append(d)
    return dirs, own


def discover_transcript(cwd: Path | str, home: Path | None = None,
                        session_id: str | None = None) -> Discovery:
    """Locate the transcript to grade for ``cwd``.

    Order of preference:
      1. The ACTIVE session: when ``CLAUDE_CODE_SESSION_ID`` names a ``<id>.jsonl``
         in the cwd's own or any ancestor project dir, that IS the current session
         -- authoritative, regardless of which file is newest.
      2. Heuristic fallback (no session id, or its file isn't here): the most
         recently active non-headless transcript that lives in cwd's own project
         dir or whose content references cwd. ``Discovery.ambiguous`` flags when
         this pick was made with other candidates present, so the caller can fail
         safe instead of silently grading the wrong session.
    """
    base = transcript_base(home)
    cwd_resolved = str(Path(cwd).resolve())
    dirs, own_dirs = _candidate_project_dirs(cwd, base)

    sid = session_id if session_id is not None else active_session_id()
    if sid:
        for d in dirs:
            cand = d / f"{sid}.jsonl"
            if cand.is_file():
                return Discovery(path=cand, source="session")

    candidates: list[Path] = []
    for d in dirs:
        candidates.extend(d.glob("*.jsonl"))
    candidates = [p for p in candidates if not is_headless_transcript(p)]
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    for i, path in enumerate(candidates):
        if str(path.parent) in own_dirs:
            return Discovery(path=path, source="own", alternatives=len(candidates) - 1)
        if _mentions_path(path, cwd_resolved):
            return Discovery(path=path, source="reference", alternatives=len(candidates) - 1)
    return Discovery()


def latest_transcript_for(cwd: Path | str, home: Path | None = None) -> Path | None:
    """The transcript path to grade for ``cwd`` (back-compat thin wrapper)."""
    return discover_transcript(cwd, home).path


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


# Cap on captured per-result output; the contradiction engine only needs the
# failure lines, which are tiny relative to a full build/test log.
_OUTPUT_CAP = 8000


def _result_content(line: dict) -> str:
    """Concatenate the textual output of a tool result (stdout/stderr)."""
    parts: list[str] = []
    msg = line.get("message") or {}
    content = msg.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                c = block.get("content")
                if isinstance(c, str):
                    parts.append(c)
                elif isinstance(c, list):
                    for sub in c:
                        if isinstance(sub, dict) and sub.get("type") == "text":
                            parts.append(sub.get("text", ""))
    tur = line.get("toolUseResult")
    if isinstance(tur, dict):
        for key in ("stdout", "stderr"):
            v = tur.get(key)
            if isinstance(v, str) and v:
                parts.append(v)
    return "\n".join(p for p in parts if p)[:_OUTPUT_CAP]


def _result_exit_code(line: dict) -> int | None:
    """Best-effort exit code from a tool result (varies by Claude Code version)."""
    tur = line.get("toolUseResult")
    if isinstance(tur, dict):
        for key in ("exitCode", "exit_code", "returncode", "code"):
            v = tur.get(key)
            if isinstance(v, int):
                return v
    return None


def parse_transcript(path: Path | str) -> Transcript:
    """Parse a transcript JSONL into the essentials the probes need."""
    path = Path(path)
    t = Transcript(path=str(path))
    pending: dict[str, tuple[str, str, str]] = {}  # tool_use_id -> (tool, label, command)
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
                        command = _tool_command(tool, inp)
                        if tid:
                            pending[tid] = (tool, label, command)
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
                tool, label, command = pending.get(tid or "", ("", "", ""))
                if not tool:
                    tur = line.get("toolUseResult")
                    if isinstance(tur, dict):
                        tool = tur.get("commandName", "") or "tool"
                        label = label or tur.get("commandName", "")
                t.tool_events.append(
                    ToolEvent(
                        tool=tool or "tool",
                        label=label or tool or "tool",
                        failed=failed,
                        exit_code=_result_exit_code(line),
                        command=command,
                        output=_result_content(line),
                    )
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


def _tool_command(tool: str, inp: dict) -> str:
    """The full command/target of a tool call (untruncated, for evidence)."""
    if tool == "Bash":
        return str(inp.get("command", "")).strip()
    for key in ("file_path", "notebook_path", "path"):
        if inp.get(key):
            return str(inp[key])
    return ""


def iter_tool_outputs(transcript: "Transcript | None"):
    """Yield each tool event that captured output -- the contradiction engine's
    raw material. Reuses the already-parsed transcript; never re-reads anything.
    """
    if transcript is None:
        return
    for ev in transcript.tool_events:
        yield ev

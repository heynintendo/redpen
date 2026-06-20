"""SQLite ledger of every verdict RedPen has rendered.

Stored at ``<project>/.redpen/ledger.db``. The point is continuity across
sessions: a later run (or a later Claude Code) can ask "what was claimed
before, and did it hold?" -- turning RedPen into a memory of broken promises.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import LEDGER_DB, LEDGER_DIR
from .engine import Finding

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    session_id TEXT,
    claim      TEXT NOT NULL,
    probe      TEXT NOT NULL,
    verdict    TEXT NOT NULL,
    detail     TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_ts ON runs(ts);
"""


@dataclass
class HistoryRow:
    ts: str
    session_id: str
    claim: str
    probe: str
    verdict: str
    detail: str


def ledger_path(project_root: Path | str) -> Path:
    return Path(project_root) / LEDGER_DIR / LEDGER_DB


def _connect(project_root: Path | str) -> sqlite3.Connection:
    path = ledger_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    return conn


def record(project_root: Path | str, findings: list[Finding], session_id: str = "") -> int:
    """Append every finding from a run. Returns the number of rows written."""
    if not findings:
        return 0
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = [
        (ts, session_id, f.display, f.result.probe, f.result.verdict.value, f.result.detail)
        for f in findings
    ]
    conn = _connect(project_root)
    try:
        conn.executemany(
            "INSERT INTO runs (ts, session_id, claim, probe, verdict, detail) VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()
    return len(rows)


def history(project_root: Path | str, limit: int = 20) -> list[HistoryRow]:
    """Most recent verdicts first."""
    path = ledger_path(project_root)
    if not path.exists():
        return []
    conn = _connect(project_root)
    try:
        cur = conn.execute(
            "SELECT ts, session_id, claim, probe, verdict, detail FROM runs ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [HistoryRow(*row) for row in cur.fetchall()]
    finally:
        conn.close()

"""Persist the full evidence of the last `redpen check` run.

Written to ``.redpen/last_run.json`` so `redpen explain <n>` can make any
verdict fully auditable: the claim, the probe, the exact commands run, the raw
evidence, and the one-line reason.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import LEDGER_DIR

LAST_RUN_FILE = "last_run.json"


def last_run_path(project_root: Path | str) -> Path:
    return Path(project_root) / LEDGER_DIR / LAST_RUN_FILE


def save_last_run(
    project_root: Path | str,
    findings,
    *,
    session_id: str = "",
    audit: list[dict] | None = None,
    elapsed: float | None = None,
) -> Path:
    counts = {"OK": 0, "FAIL": 0, "UNVERIFIABLE": 0}
    records = []
    for i, f in enumerate(findings, start=1):
        counts[f.result.verdict.value] = counts.get(f.result.verdict.value, 0) + 1
        records.append(
            {
                "n": i,
                "claim": f.claim_text,
                "subject": f.display,
                "source": f.source,
                "probe": f.result.probe,
                "verdict": f.result.verdict.value,
                "reason": f.result.detail,
                "commands": list(f.result.evidence.get("commands", [])),
                "evidence": f.result.evidence,
            }
        )
    data = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session_id": session_id,
        "elapsed_seconds": round(elapsed, 4) if elapsed is not None else None,
        "summary": counts,
        "findings": records,
        "request_audit": audit or [],
    }
    path = last_run_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def load_last_run(project_root: Path | str) -> dict | None:
    path = last_run_path(project_root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None

"""Execute one Case end-to-end: build its workspace, run `redpen check --json` as
a subprocess (the real CLI, the real engine, the real ledger), parse the result,
and classify it against the case's ground truth.

We run redpen via ``python -m redpen.cli`` against the working-tree source (not
any installed copy) so the suite always grades the current code.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .builders import Rng
from .model import Case, CaseResult, classify

REPO_ROOT = Path(__file__).resolve().parents[3]


def case_seed(cid: str) -> int:
    """Stable per-case seed derived from the case id (order-independent)."""
    return int.from_bytes(hashlib.sha256(cid.encode("utf-8")).digest()[:4], "big")


def redpen_argv() -> list:
    return [sys.executable, "-m", "redpen.cli", "check"]


def _parse_json(stdout: str):
    """Extract the JSON object redpen prints (it may be preceded by a few dim
    informational lines like 'transcript: <path>')."""
    i = stdout.find("{")
    if i == -1:
        return None
    try:
        return json.loads(stdout[i:])
    except (json.JSONDecodeError, ValueError):
        # Be forgiving: try line-trimmed tail starting at the last bare '{'.
        for start in range(len(stdout)):
            if stdout[start] == "{":
                try:
                    return json.loads(stdout[start:])
                except (json.JSONDecodeError, ValueError):
                    continue
        return None


def base_env(home: Path) -> dict:
    """Deterministic, isolated environment for the redpen subprocess."""
    env = dict(os.environ)
    pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(REPO_ROOT) + (os.pathsep + pp if pp else "")
    # Isolate from the user's git config and any real Claude transcripts; keep a
    # minimal PATH so gh/claude are absent unless a case fakes them.
    env["PATH"] = os.pathsep.join(["/usr/bin", "/bin", os.path.dirname(sys.executable)])
    env["HOME"] = str(home)
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.pop("REDPEN_HOOK", None)  # never the recursion-guarded hook path
    return env


def run_redpen(cwd, *, transcript=None, extra_args=(), env_extra=None, home=None,
               timeout: float = 90.0) -> dict:
    """Invoke `redpen check --json` once. Returns a dict with rc/data/stdout/...

    Standalone (no Case) so the concurrency soak can hammer prebuilt repos.
    """
    cwd = Path(cwd)
    if home is None:
        home = cwd.parent / "_home"
    Path(home).mkdir(parents=True, exist_ok=True)
    env = base_env(Path(home))
    if env_extra:
        env.update(env_extra)

    cmd = redpen_argv()
    if transcript is not None:
        cmd += ["--transcript", str(transcript)]
    cmd += ["--json", "--no-art", "--no-color"]
    cmd += list(extra_args)

    t0 = time.perf_counter()
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"timeout": True, "rc": -1, "data": None, "stdout": "", "stderr": "timeout",
                "wall_ms": (time.perf_counter() - t0) * 1000.0}
    wall_ms = (time.perf_counter() - t0) * 1000.0
    data = _parse_json(proc.stdout)
    if data is None and proc.returncode == 0:
        data = {"findings": [], "summary": {"OK": 0, "FAIL": 0, "UNVERIFIABLE": 0}, "elapsed_seconds": None}
    return {"timeout": False, "rc": proc.returncode, "data": data, "stdout": proc.stdout,
            "stderr": proc.stderr, "wall_ms": wall_ms}


def run_case(case: Case, workspace, *, timeout: float = 90.0) -> CaseResult:
    """Build and run a single case, returning its classified result."""
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    rng = Rng(case_seed(case.cid))

    try:
        built = case.build(workspace, rng)
    except Exception as exc:  # noqa: BLE001 -- a build crash is a case failure, not a suite crash
        res = CaseResult(cid=case.cid, axis=case.axis, title=case.title, passed=False)
        res.error = f"build error: {type(exc).__name__}: {exc}"
        return res

    home = workspace / "_home"
    out = run_redpen(built.cwd, transcript=built.transcript, extra_args=built.extra_args,
                     env_extra=built.env, home=home, timeout=timeout)

    if out["timeout"]:
        res = CaseResult(cid=case.cid, axis=case.axis, title=case.title, passed=False)
        res.error = f"redpen TIMED OUT after {timeout}s (deterministic path must never hang)"
        res.wall_ms = out["wall_ms"]
        return res

    data = out["data"]
    if data is None:
        res = CaseResult(cid=case.cid, axis=case.axis, title=case.title, passed=False)
        res.exit_code = out["rc"]
        res.wall_ms = out["wall_ms"]
        res.error = (f"no parseable JSON (rc={out['rc']}); "
                     f"stderr={out['stderr'][:300]!r}; stdout={out['stdout'][:200]!r}")
        return res

    findings = [
        {"probe": f.get("probe", ""), "subject": f.get("subject", ""), "verdict": f.get("verdict", "")}
        for f in data.get("findings", [])
    ]
    res = classify(case, findings)
    res.elapsed = data.get("elapsed_seconds")
    res.wall_ms = out["wall_ms"]
    res.exit_code = out["rc"]

    # CLI invariant: exit code is 1 iff some finding is FAIL.
    any_fail = any(f["verdict"] == "FAIL" for f in findings)
    if out["rc"] not in (0, 1):
        res.error = f"unexpected exit code {out['rc']}"
        res.passed = False
    elif (out["rc"] == 1) != any_fail:
        res.soft.append(f"exit code {out['rc']} inconsistent with FAIL-present={any_fail}")
        res.passed = False

    if case.invariant is not None:
        why = case.invariant(data, out["rc"], out["stderr"] or "")
        if why:
            res.soft.append(f"invariant: {why}")
            res.passed = False

    return res

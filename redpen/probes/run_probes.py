"""Command-running probes: tests_pass, build_ok, lint_clean.

These detect the relevant command from project files. By default RedPen is
read-only: it checks whether the command *already ran* this session (from the
transcript) and what its exit code was. It re-runs the command only when
``--run`` is passed (ctx.run is True), which intentionally spends more than the
2s deterministic budget.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ..util import RC_NOT_FOUND, RC_TIMEOUT, run
from .base import ProbeContext, ProbeResult, fail, ok, unverifiable

# Side-effecting commands get a longer leash than the default probe timeout.
_RUN_TIMEOUT = 180


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _package_scripts(cwd: Path) -> dict:
    pkg = cwd / "package.json"
    if not pkg.is_file():
        return {}
    try:
        return (json.loads(_read(pkg)) or {}).get("scripts", {}) or {}
    except json.JSONDecodeError:
        return {}


def _makefile_targets(cwd: Path) -> set[str]:
    targets: set[str] = set()
    for name in ("Makefile", "makefile", "GNUmakefile"):
        text = _read(cwd / name)
        for line in text.splitlines():
            if ":" in line and not line.startswith((" ", "\t", "#")):
                targets.add(line.split(":", 1)[0].strip())
    return targets


# --- command detection ------------------------------------------------------


def detect_test_command(cwd: Path) -> tuple[list[str], str] | None:
    scripts = _package_scripts(cwd)
    test_script = scripts.get("test", "")
    if test_script and "no test specified" not in test_script:
        return (["npm", "test", "--silent"], "npm test")

    has_pytest_cfg = (
        "pytest" in _read(cwd / "pyproject.toml")
        or (cwd / "pytest.ini").is_file()
        or "pytest" in _read(cwd / "setup.cfg")
        or "[tool:pytest]" in _read(cwd / "tox.ini")
    )
    has_tests = (
        (cwd / "tests").is_dir()
        or any(cwd.glob("test_*.py"))
        or any(cwd.glob("*_test.py"))
    )
    if has_pytest_cfg or has_tests:
        # Run via the active interpreter so pytest resolves from the same env
        # RedPen runs in, rather than relying on `pytest` being on PATH.
        return ([sys.executable, "-m", "pytest", "-q"], "pytest")

    if (cwd / "Cargo.toml").is_file():
        return (["cargo", "test"], "cargo test")
    if (cwd / "go.mod").is_file():
        return (["go", "test", "./..."], "go test ./...")
    if "test" in _makefile_targets(cwd):
        return (["make", "test"], "make test")
    return None


def detect_build_command(cwd: Path) -> tuple[list[str], str] | None:
    if "build" in _package_scripts(cwd):
        return (["npm", "run", "build"], "npm run build")
    if "build" in _makefile_targets(cwd):
        return (["make", "build"], "make build")
    if (cwd / "Cargo.toml").is_file():
        return (["cargo", "build"], "cargo build")
    if (cwd / "go.mod").is_file():
        return (["go", "build", "./..."], "go build ./...")
    return None


def detect_lint_command(cwd: Path) -> tuple[list[str], str] | None:
    if "[tool.ruff]" in _read(cwd / "pyproject.toml") or (cwd / "ruff.toml").is_file() or (cwd / ".ruff.toml").is_file():
        return (["ruff", "check", "."], "ruff check .")
    if (cwd / ".flake8").is_file() or "[flake8]" in _read(cwd / "setup.cfg"):
        return (["flake8"], "flake8")
    if "lint" in _package_scripts(cwd):
        return (["npm", "run", "lint"], "npm run lint")
    if any((cwd / n).exists() for n in (".eslintrc", ".eslintrc.js", ".eslintrc.json", "eslint.config.js", "eslint.config.mjs")):
        return (["npx", "eslint", "."], "eslint")
    return None


# --- transcript lookup ------------------------------------------------------


def _find_run_in_transcript(ctx: ProbeContext, keywords: list[str]):
    """Return the most recent Bash ToolEvent matching any keyword, else None."""
    if ctx.transcript is None:
        return None
    match = None
    for ev in ctx.transcript.tool_events:
        label = ev.label.lower()
        if ev.tool == "Bash" and any(k in label for k in keywords):
            match = ev
    return match


# --- probes -----------------------------------------------------------------


def _run_gated(ctx, probe, detect, transcript_keywords):
    detected = detect(Path(ctx.cwd))
    if detected is None:
        return unverifiable(probe, "no command detected for this project")
    cmd, display = detected

    if ctx.run:
        rc, out, err = run(cmd, cwd=ctx.cwd, timeout=_RUN_TIMEOUT)
        # Could-not-run is absent evidence, never a contradiction. A missing
        # executable or a timeout is UNVERIFIABLE, not FAIL -- don't cry wolf.
        if rc == RC_NOT_FOUND:
            return unverifiable(probe, f"could not run `{display}` (executable not found)", command=display)
        if rc == RC_TIMEOUT:
            return unverifiable(probe, f"`{display}` timed out before finishing", command=display)
        tail = (err or out).strip().splitlines()[-1:] or [""]
        if rc == 0:
            return ok(probe, f"`{display}` exited 0", command=display, exit_code=0, ran=True)
        return fail(
            probe,
            f"`{display}` exited {rc}",
            command=display,
            exit_code=rc,
            ran=True,
            tail=tail[0][:200],
        )

    ev = _find_run_in_transcript(ctx, transcript_keywords)
    if ev is None:
        return unverifiable(
            probe,
            f"`{display}` was not run this session -- pass --run to execute it",
            command=display,
            ran=False,
        )
    if ev.failed:
        return fail(probe, f"`{display}` failed this session", command=display, ran=True, source="transcript")
    return ok(probe, f"`{display}` ran clean this session", command=display, ran=True, source="transcript")


def tests_pass(ctx: ProbeContext, **_: object) -> ProbeResult:
    return _run_gated(ctx, "tests_pass", detect_test_command, ["pytest", "npm test", "npm run test", "cargo test", "go test", "make test"])


def build_ok(ctx: ProbeContext, **_: object) -> ProbeResult:
    return _run_gated(ctx, "build_ok", detect_build_command, ["npm run build", "make build", "cargo build", "go build"])


def lint_clean(ctx: ProbeContext, **_: object) -> ProbeResult:
    return _run_gated(ctx, "lint_clean", detect_lint_command, ["ruff", "flake8", "npm run lint", "eslint"])

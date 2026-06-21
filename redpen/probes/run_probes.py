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

from ..contradiction import find_failures
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


def _execute_run(ctx, probe, cmd, display):
    """The explicit, off-by-default --run last resort (re-runs the command)."""
    rc, out, err = run(cmd, cwd=ctx.cwd, timeout=_RUN_TIMEOUT)
    # Could-not-run is absent evidence, never a contradiction. A missing
    # executable or a timeout is UNVERIFIABLE, not FAIL -- don't cry wolf.
    if rc == RC_NOT_FOUND:
        return unverifiable(probe, f"couldn't run `{display}` — it isn't installed", command=display)
    if rc == RC_TIMEOUT:
        return unverifiable(probe, f"`{display}` timed out before it finished", command=display, exit_code=RC_TIMEOUT)
    # pytest exit 5 == "no tests were collected". Nothing ran -> UNVERIFIABLE.
    if display == "pytest" and rc == 5:
        return unverifiable(probe, "pytest didn't collect any tests, so there's nothing to confirm", command=display, exit_code=5)
    tail = (err or out).strip().splitlines()[-1:] or [""]
    if rc == 0:
        return ok(probe, f"`{display}` passed when I re-ran it", command=display, exit_code=0, ran=True)
    return fail(probe, f"`{display}` failed when I re-ran it (exit {rc})", command=display, exit_code=rc, ran=True, tail=tail[0][:200])


def _run_gated(ctx, probe, detect, transcript_keywords, *, kind=None, multi_runner=None, noun="it"):
    """Transcript-primary verification (default), --run as an explicit last resort.

    Order on the default path: an incontestable contradiction in the agent's own
    captured output -> FAIL; an ambiguous monorepo (multiple runners) ->
    UNVERIFIABLE; the command never ran this session -> UNVERIFIABLE (we do NOT
    re-run); it ran and failed -> FAIL; it ran clean -> OK.
    """
    detected = detect(Path(ctx.cwd))
    cmd, display = detected if detected else (None, None)

    if ctx.run and cmd is not None:
        return _execute_run(ctx, probe, cmd, display)

    # Transcript is primary -- and works with no project-file detection and no
    # git: the run in the transcript is the evidence.
    # 1) incontestable: the agent's own output shows a failure of this kind.
    if kind and ctx.transcript is not None:
        fails = find_failures(ctx.transcript.tool_events, kind)
        if fails:
            f0 = fails[0]
            return fail(
                probe,
                f"the {kind} failed this session — {f0.line[:60]}",
                command=(f0.command or display or kind)[:200],
                contradiction=f0.line,
                source="transcript-output",
            )

    # 2) monorepo / ambiguity guard -- never a false OK when we can't tell which.
    if multi_runner is not None:
        runners = multi_runner(Path(ctx.cwd))
        if len(runners) > 1:
            return unverifiable(
                probe,
                f"there's more than one runner here ({', '.join(runners)}), so I can't tell which one '{noun}' means",
                runners_detected=runners,
            )

    # 3) did the command run this session? (we never re-run on the default path)
    ev = _find_run_in_transcript(ctx, transcript_keywords)
    if ev is not None:
        label = display or (ev.command or ev.label)
        if ev.failed:
            return fail(probe, f"`{label}` failed this session", command=label, ran=True, source="transcript")
        return ok(probe, f"`{label}` ran and passed this session", command=label, ran=True, source="transcript")

    # 4) nothing ran this session.
    if detected is None:
        return unverifiable(probe, f"nothing ran this session and there's no runner here, so I can't confirm {noun}", ran=False)
    return unverifiable(
        probe,
        f"`{display}` wasn't run this session, so I can't confirm {noun} — pass --run to run it (slow, side-effecting)",
        command=display,
        ran=False,
    )


def _present_test_runners(cwd: Path) -> list[str]:
    """All test runners configured in this project (for monorepo/ambiguity notes)."""
    runners: list[str] = []
    scripts = _package_scripts(cwd)
    if scripts.get("test") and "no test specified" not in scripts.get("test", ""):
        runners.append("npm test")
    if (
        "pytest" in _read(cwd / "pyproject.toml")
        or (cwd / "pytest.ini").is_file()
        or "pytest" in _read(cwd / "setup.cfg")
        or "[tool:pytest]" in _read(cwd / "tox.ini")
        or (cwd / "tests").is_dir()
        or any(cwd.glob("test_*.py"))
        or any(cwd.glob("*_test.py"))
    ):
        runners.append("pytest")
    if (cwd / "Cargo.toml").is_file():
        runners.append("cargo test")
    if (cwd / "go.mod").is_file():
        runners.append("go test")
    if "test" in _makefile_targets(cwd):
        runners.append("make test")
    return runners


def tests_pass(ctx: ProbeContext, **_: object) -> ProbeResult:
    res = _run_gated(
        ctx, "tests_pass", detect_test_command,
        ["pytest", "npm test", "npm run test", "cargo test", "go test", "make test"],
        kind="tests", multi_runner=_present_test_runners, noun="the tests pass",
    )
    # Surface multi-runner ambiguity on the --run path too (the gate only does
    # it on the transcript path).
    runners = _present_test_runners(Path(ctx.cwd))
    if len(runners) > 1 and "runners_detected" not in res.evidence:
        res.evidence["runners_detected"] = runners
        res.evidence["note"] = f"multiple test runners present ({', '.join(runners)})"
    return res


def build_ok(ctx: ProbeContext, **_: object) -> ProbeResult:
    return _run_gated(
        ctx, "build_ok", detect_build_command,
        ["npm run build", "make build", "cargo build", "go build"], kind="build", noun="the build works",
    )


def lint_clean(ctx: ProbeContext, **_: object) -> ProbeResult:
    return _run_gated(
        ctx, "lint_clean", detect_lint_command, ["ruff", "flake8", "npm run lint", "eslint"],
        noun="the linter is clean",
    )

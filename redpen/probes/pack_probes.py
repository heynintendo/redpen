"""Cheap probe-pack: dep_present, typecheck_clean, test_count, symbol_exists.

All transcript-first and precision-safe: they never auto-execute anything, and
absent/ambiguous evidence is UNVERIFIABLE, never FAIL.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from ..contradiction import find_failures
from .base import ProbeContext, ProbeResult, fail, ok, unverifiable
from .run_probes import _find_run_in_transcript


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _toml(path: Path) -> dict:
    try:
        return tomllib.loads(_read(path)) if path.is_file() else {}
    except (tomllib.TOMLDecodeError, ValueError):
        return {}


def _norm(name: str) -> str:
    return name.strip().lower().replace("_", "-")


# --- dep_present ------------------------------------------------------------


def _manifest_deps(cwd: Path) -> set[str]:
    deps: set[str] = set()
    pkg = cwd / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(_read(pkg))
            for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
                deps.update(data.get(key, {}) or {})
        except json.JSONDecodeError:
            pass
    pyproject = _toml(cwd / "pyproject.toml")
    project = pyproject.get("project", {})
    for spec in project.get("dependencies", []) or []:
        deps.add(re.split(r"[<>=!~\[ ]", str(spec), 1)[0])
    for extra in (project.get("optional-dependencies", {}) or {}).values():
        for spec in extra or []:
            deps.add(re.split(r"[<>=!~\[ ]", str(spec), 1)[0])
    poetry = pyproject.get("tool", {}).get("poetry", {}).get("dependencies", {})
    deps.update(poetry.keys())
    req = cwd / "requirements.txt"
    if req.is_file():
        for ln in _read(req).splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                deps.add(re.split(r"[<>=!~\[ ]", ln, 1)[0])
    cargo = _toml(cwd / "Cargo.toml")
    deps.update((cargo.get("dependencies", {}) or {}).keys())
    deps.update((cargo.get("dev-dependencies", {}) or {}).keys())
    return {_norm(d) for d in deps if d}


_LOCKFILES = (
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "poetry.lock", "uv.lock", "Pipfile.lock", "Cargo.lock",
)


def _lockfile_has(cwd: Path, name: str) -> bool:
    norm = _norm(name)
    # Match the name as a distinct token (tolerant of npm "node_modules/<name>",
    # poetry/cargo `name = "<name>"`), trying both dash and underscore variants.
    variants = {norm, norm.replace("-", "_")}
    pats = [re.compile(rf"(?<![\w-]){re.escape(v)}(?![\w-])", re.IGNORECASE) for v in variants]
    for lf in _LOCKFILES:
        p = cwd / lf
        if p.is_file():
            text = _read(p).lower()
            if any(pat.search(text) for pat in pats):
                return True
    return False


def dep_present(ctx: ProbeContext, name: str | None = None, **_: object) -> ProbeResult:
    """A claimed dependency must be in BOTH the manifest and the lockfile."""
    if not name:
        return unverifiable("dep_present", "no dependency name to check")
    cwd = Path(ctx.cwd)
    in_manifest = _norm(name) in _manifest_deps(cwd)
    in_lock = _lockfile_has(cwd, name)
    ev = {"dependency": name, "in_manifest": in_manifest, "in_lockfile": in_lock}
    if in_manifest and in_lock:
        return ok("dep_present", f"{name} is in both the manifest and the lockfile", **ev)
    if not in_manifest and not in_lock:
        return fail("dep_present", f"{name} isn't in the manifest or the lockfile", **ev)
    if in_manifest and not in_lock:
        return unverifiable("dep_present", f"{name} is declared in the manifest but isn't in the lockfile — looks like it wasn't installed", **ev)
    return unverifiable("dep_present", f"{name} is in the lockfile but isn't declared in the manifest", **ev)


# --- typecheck_clean --------------------------------------------------------


def _typechecker(cwd: Path) -> str | None:
    pyproject = _read(cwd / "pyproject.toml")
    if "[tool.mypy]" in pyproject or (cwd / "mypy.ini").is_file() or "[mypy]" in _read(cwd / "setup.cfg"):
        return "mypy"
    if "[tool.pyright]" in pyproject or (cwd / "pyrightconfig.json").is_file():
        return "pyright"
    if (cwd / "tsconfig.json").is_file():
        return "tsc"
    return None


_TYPECHECK_OK = re.compile(r"(?i)Success: no issues|0 errors|no errors found")
_TYPECHECK_BAD = re.compile(r"(?i)Found \d+ error|error TS\d+|\berror:")


def typecheck_clean(ctx: ProbeContext, **_: object) -> ProbeResult:
    """Verify a configured type checker ran clean -- from the transcript only."""
    tool = _typechecker(Path(ctx.cwd))
    if tool is None:
        return unverifiable("typecheck_clean", "no type checker (mypy/pyright/tsc) is set up here")
    ev = _find_run_in_transcript(ctx, [tool, "mypy", "pyright", "tsc", "typecheck", "type-check"])
    if ev is None:
        return unverifiable("typecheck_clean", f"{tool} wasn't run this session, so I can't confirm the types are clean", tool=tool)
    out = ev.output or ""
    if _TYPECHECK_BAD.search(out) or ev.failed:
        line = next((ln for ln in out.splitlines() if _TYPECHECK_BAD.search(ln)), f"{tool} reported errors")
        return fail("typecheck_clean", f"{tool} reported type errors this session — {line[:60]}", tool=tool, contradiction=line[:200])
    if _TYPECHECK_OK.search(out):
        return ok("typecheck_clean", f"{tool} ran clean this session", tool=tool)
    return unverifiable("typecheck_clean", f"{tool} ran, but its output doesn't clearly say pass or fail", tool=tool)


# --- test_count -------------------------------------------------------------

_PASS_COUNT = [
    re.compile(r"(\d+) passed"),                  # pytest
    re.compile(r"Tests:\s+(\d+) passed"),         # jest
    re.compile(r"(\d+) tests? passed"),           # generic / mocha
    re.compile(r"test result: ok\. (\d+) passed"),  # cargo
]


def _parse_pass_count(text: str) -> int | None:
    for pat in _PASS_COUNT:
        m = pat.search(text)
        if m:
            return int(m.group(1))
    return None


def test_count(ctx: ProbeContext, n: int | None = None, **_: object) -> ProbeResult:
    """For "all N tests pass": verify the count from the test run in the transcript."""
    if n is None:
        return unverifiable("test_count", "no test count to check")
    ev = _find_run_in_transcript(ctx, ["pytest", "test", "jest", "vitest", "cargo test", "go test"])
    if ev is None:
        return unverifiable("test_count", f"no test run this session, so I can't confirm {n} passing", claimed=n)
    # A real failure in the output contradicts "all N pass" outright.
    if find_failures([ev], "tests"):
        return fail("test_count", f"you said all {n} tests pass, but the test run has failures", claimed=n)
    actual = _parse_pass_count(ev.output or "")
    if actual is None:
        return unverifiable("test_count", f"couldn't read a pass count from the test run (you said {n})", claimed=n)
    if actual == n:
        return ok("test_count", f"{n} tests passed, just as you said", claimed=n, actual=actual)
    return fail("test_count", f"you said {n} tests pass, but the run shows {actual}", claimed=n, actual=actual)


# --- symbol_exists ----------------------------------------------------------

_TEXT_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".rb", ".java",
            ".c", ".h", ".cpp", ".cc", ".cs", ".php", ".swift", ".kt", ".scala", ".md"}


def _defines_symbol(text: str, sym: str) -> bool:
    s = re.escape(sym)
    patterns = [
        rf"\bdef\s+{s}\b", rf"\bclass\s+{s}\b", rf"\b(async\s+)?function\s+{s}\b",
        rf"\bfn\s+{s}\b", rf"\bfunc\s+{s}\b", rf"\b{s}\s*=\s*(\(|function|async)",
        rf"\b{s}\s*:\s*", rf"@\w+\([^)]*{s}", rf"['\"]/{s}\b",  # route-ish
        rf"\b{s}\b",  # last resort: any mention in a changed file
    ]
    return any(re.search(p, text) for p in patterns)


def symbol_exists(ctx: ProbeContext, symbol: str | None = None, **_: object) -> ProbeResult:
    """For "added function/class/endpoint X": grep the session changed-set files."""
    if not symbol:
        return unverifiable("symbol_exists", "no symbol name to check")
    cs = ctx.changed_set
    if cs is None or not cs.paths:
        return unverifiable("symbol_exists", f"no files changed this session to look for `{symbol}` in", symbol=symbol)

    hits: list[str] = []
    scanned = 0
    for abspath in cs.paths:
        p = Path(abspath)
        if p.suffix not in _TEXT_EXT or not p.is_file():
            continue
        scanned += 1
        if _defines_symbol(_read(p), symbol):
            hits.append(abspath)
    if hits:
        return ok("symbol_exists", f"found `{symbol}` in {len(hits)} file(s) you changed this session", symbol=symbol, files=hits[:10])
    # Precision-safe: not finding it in our captured changed-set isn't proof it
    # wasn't added (the change-set can be incomplete) -> UNVERIFIABLE, not FAIL.
    return unverifiable(
        "symbol_exists",
        f"didn't find `{symbol}` in the {scanned} file(s) you changed this session (it may be elsewhere)",
        symbol=symbol, scanned=scanned,
    )

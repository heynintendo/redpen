"""Filesystem probes: file_present, todos_remaining."""

from __future__ import annotations

import re

from .base import ProbeContext, ProbeResult, fail, ok, unverifiable

# Strong signals that a "touched" file is not actually finished.
_STUB_RE = re.compile(r"\braise\s+NotImplementedError\b")
_TODO_RE = re.compile(r"\b(TODO|FIXME|XXX)\b")


def file_present(ctx: ProbeContext, path: str | None = None, **_: object) -> ProbeResult:
    """Verify a "created/wrote <path>" claim.

    Success condition: the file exists and is non-empty. A missing file
    *contradicts* the claim (FAIL). An empty file contradicts a "wrote
    content" claim too, but with a distinct detail so the user can tell the
    difference. A present, non-empty file is OK and we report its mtime.
    """
    if not path:
        return unverifiable("file_present", "no path supplied to verify")

    p = ctx.resolve(path)
    if not p.exists():
        return fail("file_present", f"{path} does not exist", exists=False, path=str(p))

    if p.is_dir():
        # A directory satisfies "created <dir>" if it has any contents.
        entries = list(p.iterdir())
        if entries:
            return ok(
                "file_present",
                f"{path}/ exists ({len(entries)} entries)",
                exists=True,
                is_dir=True,
                entries=len(entries),
            )
        return fail("file_present", f"{path}/ exists but is empty", exists=True, is_dir=True, entries=0)

    stat = p.stat()
    size = stat.st_size
    mtime = stat.st_mtime
    if size == 0:
        return fail(
            "file_present",
            f"{path} exists but is empty (0 bytes)",
            exists=True,
            size=0,
            mtime=mtime,
        )

    return ok(
        "file_present",
        f"{path} present ({size} bytes)",
        exists=True,
        size=size,
        mtime=mtime,
    )


def todos_remaining(ctx: ProbeContext, **_: object) -> ProbeResult:
    """Scan files touched this session for new stubs / TODO markers.

    A ``raise NotImplementedError`` in a file the assistant just edited is a
    clear contradiction of a "done/implemented" claim -> FAIL. Plain
    TODO/FIXME markers are ambiguous (they may predate the session or be
    intentional), so they are UNVERIFIABLE, never FAIL.
    """
    if ctx.transcript is None or not ctx.transcript.touched_files:
        return unverifiable(
            "todos_remaining",
            "no touched files known from the transcript",
            touched_files=[],
        )

    stubs: list[str] = []
    todos: list[str] = []
    scanned: list[str] = []
    for rel in ctx.transcript.touched_files:
        p = ctx.resolve(rel)
        if not p.is_file():
            continue
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        scanned.append(rel)
        for i, line in enumerate(text.splitlines(), start=1):
            if _STUB_RE.search(line):
                stubs.append(f"{rel}:{i}")
            elif _TODO_RE.search(line):
                todos.append(f"{rel}:{i}")

    if stubs:
        return fail(
            "todos_remaining",
            f"{len(stubs)} unimplemented stub(s) in touched files",
            stubs=stubs[:20],
            todos=todos[:20],
            scanned=scanned,
        )
    if todos:
        return unverifiable(
            "todos_remaining",
            f"{len(todos)} TODO/FIXME marker(s) in touched files (review manually)",
            todos=todos[:20],
            scanned=scanned,
        )
    if not scanned:
        return unverifiable("todos_remaining", "touched files no longer present to scan")
    return ok("todos_remaining", f"no stubs or TODO markers in {len(scanned)} touched file(s)", scanned=scanned)

"""Filesystem probes: file_present, todos_remaining."""

from __future__ import annotations

import re

from .base import ProbeContext, ProbeResult, fail, ok, unverifiable

# Strong signals that a "touched" file is not actually finished.
_STUB_RE = re.compile(r"\braise\s+NotImplementedError\b")
_TODO_RE = re.compile(r"\b(TODO|FIXME|XXX)\b")


# Files this small get read to check for whitespace-only content; larger files
# are taken as having real content without a read.
_WHITESPACE_SCAN_LIMIT = 64 * 1024


def file_present(ctx: ProbeContext, path: str | None = None, **_: object) -> ProbeResult:
    """Verify a "created/wrote <path>" claim. Precision-first.

    Only a *missing* path contradicts "created <path>" -> FAIL. Everything
    ambiguous is UNVERIFIABLE, never FAIL: an empty file, a whitespace-only
    file, an empty directory, or a symlink whose target is missing. A present,
    non-empty file (or non-empty directory) is OK. Resolves relative paths
    against the project root; absolute paths are used as-is.
    """
    if not path:
        return unverifiable("file_present", "no path supplied to verify")

    p = ctx.resolve(path)

    # A symlink that points nowhere exists as a link but has no content -> can't
    # confirm the claim, and its absence-of-target isn't a contradiction.
    if p.is_symlink() and not p.exists():
        return unverifiable(
            "file_present",
            f"{path} is a symlink with a missing target",
            exists=True, symlink=True, target_exists=False, path=str(p),
        )

    if not p.exists():
        return fail("file_present", f"{path} does not exist", exists=False, path=str(p))

    if p.is_dir():
        entries = list(p.iterdir())
        if entries:
            return ok(
                "file_present",
                f"{path} exists (directory, {len(entries)} entries)",
                exists=True, is_dir=True, entries=len(entries),
            )
        return unverifiable(
            "file_present",
            f"{path} exists but is an empty directory",
            exists=True, is_dir=True, entries=0,
        )

    stat = p.stat()
    size, mtime, is_link = stat.st_size, stat.st_mtime, p.is_symlink()

    if size == 0:
        return unverifiable(
            "file_present",
            f"{path} exists but is empty (0 bytes)",
            exists=True, size=0, mtime=mtime, symlink=is_link,
        )

    if size <= _WHITESPACE_SCAN_LIMIT:
        try:
            text = p.read_text(errors="replace")
        except OSError:
            text = None
        if text is not None and text.strip() == "":
            return unverifiable(
                "file_present",
                f"{path} exists but contains only whitespace",
                exists=True, size=size, mtime=mtime,
            )

    return ok(
        "file_present",
        f"{path} present ({size} bytes)",
        exists=True, size=size, mtime=mtime, symlink=is_link,
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

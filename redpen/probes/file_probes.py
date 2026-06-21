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


def file_present(ctx: ProbeContext, path: str | None = None, created: bool = False, **_: object) -> ProbeResult:
    """Verify a "created/wrote <path>" claim. Precision-first, session-scoped.

    Only a *missing* path contradicts "created <path>" -> FAIL. Everything
    ambiguous is UNVERIFIABLE, never FAIL: an empty file, a whitespace-only
    file, an empty directory, or a symlink whose target is missing.

    For a *creation* claim (``created=True``) the file must also be in the
    session changed-set -- if it exists but the agent never touched it this
    session, the creation claim is UNVERIFIABLE, not OK (the pre-existed-file
    false-OK). Degrades to a plain existence check when no changed-set is known.
    """
    if not path:
        return unverifiable("file_present", "no file path to check")

    p = ctx.resolve(path)

    # A symlink that points nowhere exists as a link but has no content -> can't
    # confirm the claim, and its absence-of-target isn't a contradiction.
    if p.is_symlink() and not p.exists():
        return unverifiable(
            "file_present",
            f"{path} is a symlink pointing at something that isn't there",
            exists=True, symlink=True, target_exists=False, path=str(p),
        )

    if not p.exists():
        return fail("file_present", f"{path} does not exist", exists=False, path=str(p))

    # Session-scoping + authorship attribution: a "created/wrote X" claim needs
    # THIS session's own transcript edit-record (a Write/Edit tool-use on the
    # path). A git/filesystem delta is not per-session -- a file another
    # concurrent session changed shows up there too -- so it can't attribute
    # authorship. Without a transcript edit, the authorship claim is
    # UNVERIFIABLE, never OK (the cross-session false-OK guard).
    if created and ctx.changed_set is not None:
        provenance = ctx.changed_set.provenance(ctx.cwd, path)
        attributable = "transcript" in provenance
        # A "created <dir>/" claim is substantiated when the session's transcript
        # wrote files INSIDE that directory (the agent rarely "writes" a folder
        # directly -- it writes the files in it).
        if not attributable and p.is_dir():
            from ..changeset import normalize as _norm

            prefix = _norm(ctx.cwd, path).rstrip("/") + "/"
            attributable = any(
                "transcript" in tags and cs_path.startswith(prefix)
                for cs_path, tags in ctx.changed_set.paths.items()
            )
        if not attributable:
            return unverifiable(
                "file_present",
                f"{path} is there, but this session's transcript shows no edit to it — "
                "I can't attribute the authorship to this session",
                exists=True, touched_this_session=False,
                provenance=sorted(provenance), path=str(p),
            )

    if p.is_dir():
        entries = list(p.iterdir())
        if entries:
            return ok(
                "file_present",
                f"{path} is there — a directory with {len(entries)} item(s)",
                exists=True, is_dir=True, entries=len(entries),
            )
        return unverifiable(
            "file_present",
            f"{path} is there, but it's an empty directory",
            exists=True, is_dir=True, entries=0,
        )

    stat = p.stat()
    size, mtime, is_link = stat.st_size, stat.st_mtime, p.is_symlink()

    if size == 0:
        return unverifiable(
            "file_present",
            f"{path} is there, but it's empty (0 bytes)",
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
                f"{path} is there, but it's only whitespace",
                exists=True, size=size, mtime=mtime,
            )

    ev = {"exists": True, "size": size, "mtime": mtime, "symlink": is_link}
    if created and ctx.changed_set is not None:
        ev["touched_this_session"] = True
        ev["provenance"] = sorted(ctx.changed_set.provenance(ctx.cwd, path))
    return ok("file_present", f"{path} is there ({size} bytes)", **ev)


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
            "no files were edited this session, so there's nothing to scan for stubs",
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
        plural = "stub" if len(stubs) == 1 else "stubs"
        return fail(
            "todos_remaining",
            f"{len(stubs)} unfinished {plural} (raise NotImplementedError) left in files you edited",
            stubs=stubs[:20],
            todos=todos[:20],
            scanned=scanned,
        )
    if todos:
        return unverifiable(
            "todos_remaining",
            f"{len(todos)} TODO/FIXME marker(s) in files you edited — worth a look",
            todos=todos[:20],
            scanned=scanned,
        )
    if not scanned:
        return unverifiable("todos_remaining", "the files that were edited aren't there anymore to scan")
    return ok("todos_remaining", f"no stubs or TODO markers in the {len(scanned)} file(s) you edited", scanned=scanned)

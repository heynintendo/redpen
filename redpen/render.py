"""Verdict renderer: compact, fast, terminal-aware.

Leads with a plain tally (verified / can't confirm / failed), one line per
finding (marker + subject + a readable reason), and a header mascot that degrades
by terminal capability: full truecolor pixel art on truecolor TTYs, a 256-color
version on 256-color terminals, a small clean ASCII mascot as the last resort.
"""

from __future__ import annotations

import os
import shutil
import sys
import textwrap
from pathlib import Path

from .config import TOOL_NAME
from .engine import Finding, tally
from .ledger import HistoryRow
from .probes.base import Verdict

_CSI = "\033["

# A verdict marker is a glyph + a short word. Color carries OK vs FAIL (same dot
# shape); the word carries it with color off. UNSURE uses a distinct triangle.
# With color off we fall back to a clean, aligned bracket label.
_MARKERS = {
    Verdict.OK: ("●", "OK", "[OK]", "green_b"),
    Verdict.FAIL: ("●", "FAIL", "[FAIL]", "red_b"),
    Verdict.UNVERIFIABLE: ("▲", "UNSURE", "[ ? ]", "amber_b"),
}
_MARKER_WIDTH = 8  # visible width; "▲ UNSURE" is the widest
_VERDICT_BY_NAME = {v.value: v for v in Verdict}

# Plain words for the human-facing tally.
_WORD = {Verdict.OK: "verified", Verdict.FAIL: "failed", Verdict.UNVERIFIABLE: "can't confirm"}


# --- terminal capability ----------------------------------------------------
def _under_claude_code() -> bool:
    """True when running inside a Claude Code session (the `/check` path)."""
    return bool(os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE_ENTRYPOINT"))


def _color_disabled() -> bool:
    """Color is hard-off: NO_COLOR (spec), CLICOLOR=0, or our own override."""
    return bool(
        os.environ.get("NO_COLOR")
        or os.environ.get("CLICOLOR") == "0"
        or os.environ.get("REDPEN_COLOR", "").lower() in ("never", "off", "0", "false", "no")
    )


def _color_forced() -> str | None:
    """An explicit "emit color even without a TTY" opt-in, or None.

    Returns the FORCE_COLOR level hint ("1"/"2"/"3"), "" for a bare force, or
    None when color was not explicitly forced. Standard CLICOLOR_FORCE /
    FORCE_COLOR semantics, plus REDPEN_COLOR=always.
    """
    if os.environ.get("REDPEN_COLOR", "").lower() in ("always", "force", "on", "1", "true", "yes"):
        return ""
    cf = os.environ.get("CLICOLOR_FORCE")
    if cf and cf != "0":
        return ""
    fc = os.environ.get("FORCE_COLOR")
    if fc and fc != "0":
        return fc
    return None


def _color_level(stream) -> int:
    """Terminal color capability: 0 none · 1 16-color · 2 256-color · 3 truecolor.

    SAFE DEFAULT: color is emitted only to a real TTY or when explicitly forced.
    Inside a Claude Code `/check` session the output is captured (not a TTY), so
    by default we emit clean monochrome -- never raw \\x1b[ escapes that a
    consumer might show as literal garbage. A user who has confirmed their
    terminal/UI renders ANSI cleanly opts in with CLICOLOR_FORCE=1 / FORCE_COLOR
    (and can force-disable with NO_COLOR / CLICOLOR=0 / REDPEN_COLOR=never).

    Truecolor is used ONLY when COLORTERM advertises it, so 256-color terminals
    get a representation they can render instead of silently-dropped 24-bit codes.
    """
    if _color_disabled():
        return 0
    forced = _color_forced()
    isatty = bool(getattr(stream, "isatty", lambda: False)())
    if not isatty and forced is None:
        # Not a real TTY and not explicitly forced (includes the captured Claude
        # Code path) -> safe, clean monochrome.
        return 0
    colorterm = os.environ.get("COLORTERM", "").lower()
    if colorterm in ("truecolor", "24bit") or forced == "3":
        return 3
    term = os.environ.get("TERM", "")
    if "256color" in term or "256" in colorterm or forced == "2":
        return 2
    if term and term != "dumb":
        return 1
    # Forced color but the terminal advertises nothing -> 256 renders widely.
    return 2 if forced is not None else 0


def _supports_color(stream) -> bool:
    """Back-compat boolean: any color at all."""
    return _color_level(stream) >= 1


class Palette:
    """Wraps text in ANSI codes, or doesn't, depending on ``on``.

    Marker/tally colors use the bright 16-color (90-series) codes, which render
    on every color terminal -- 16, 256, and truecolor alike.
    """

    def __init__(self, on: bool):
        self.on = on

    def _w(self, code: str, s: str) -> str:
        return f"{_CSI}{code}m{s}{_CSI}0m" if self.on else s

    def red(self, s):
        return self._w("31", s)

    def green(self, s):
        return self._w("32", s)

    def yellow(self, s):
        return self._w("33", s)

    def dim(self, s):
        return self._w("2", s)

    def bold(self, s):
        return self._w("1", s)

    def bold_red(self, s):
        return self._w("1;31", s)

    def green_b(self, s):
        return self._w("1;92", s)

    def red_b(self, s):
        return self._w("1;91", s)

    def amber_b(self, s):
        return self._w("1;93", s)


def _marker(p: Palette, verdict: Verdict) -> str:
    """A fixed-width verdict marker: bright bold "● OK" / "● FAIL" / "▲ UNSURE",
    or a clean aligned "[OK]" / "[FAIL]" / "[ ? ]" with color off."""
    glyph, word, plain, paint = _MARKERS[verdict]
    if not p.on:
        return plain.ljust(_MARKER_WIDTH)
    text = f"{glyph} {word}"
    pad = " " * max(0, _MARKER_WIDTH - len(text))  # uncolored padding aligns columns
    return getattr(p, paint)(text) + pad


def _truncate(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _term_width() -> int:
    return min(max(shutil.get_terminal_size((100, 24)).columns, 60), 120)


# --- header mascot, by capability -------------------------------------------
# Shipped at docs/mascot_ansi*.txt (and mirrored into redpen/_assets for wheels).
def _art_paths(name: str):
    here = Path(__file__).resolve().parent
    return (here.parent / "docs" / name, here / "_assets" / name)


def _read_art(name: str) -> str | None:
    for path in _art_paths(name):
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8").rstrip("\n")
        except OSError:
            continue
    return None


# A polished monochrome grader for 16-color / no-color and captured contexts
# (the Claude Code /check path most users see): the same heavy-lidded, grumpy,
# unimpressed examiner as the pixel mascot, designed to read as intentional in
# plain text. Glyphs are common and render in any UTF-8 terminal/UI.
_ASCII_MASCOT = (
    "   .-‾‾‾‾‾-.\n"
    "  / ▔     ▔ \\     {tool}\n"
    " |  ●     ●  |    grades what you claimed —\n"
    " |     ▿     |    and doubts every word\n"
    "  \\_________/ ✎"
)


def load_mascot_art(level: int = 3) -> str | None:
    """The best mascot the terminal can render: truecolor (3), 256-color (2), or
    None below that (the caller shows the ASCII mascot)."""
    if level >= 3:
        return _read_art("mascot_ansi.txt") or _read_art("mascot_ansi_256.txt")
    if level == 2:
        return _read_art("mascot_ansi_256.txt") or _read_art("mascot_ansi.txt")
    return None


def _header_block(p: Palette, show_art: bool, level: int) -> str | None:
    """The header above a report, or None when --no-art is set."""
    if not show_art:
        return None
    art = load_mascot_art(level)
    if art:
        return art
    return p.bold(_ASCII_MASCOT.format(tool=TOOL_NAME))


def render_header(show_art: bool = True, color: bool | None = None) -> str:
    """Public header for callers outside the report (e.g. the no-claims path)."""
    level = _color_level(sys.stdout) if color is None else (3 if color else 0)
    block = _header_block(Palette(level >= 1), show_art, level)
    return block or ""


# --- copy: counts-first, plain ----------------------------------------------
def _tally_line(p: Palette, counts: dict) -> str:
    """The headline: the plain tally, first and unmissable."""
    return " · ".join([
        p.green_b(f"{counts[Verdict.OK]} verified"),
        p.amber_b(f"{counts[Verdict.UNVERIFIABLE]} can't confirm"),
        p.red_b(f"{counts[Verdict.FAIL]} failed"),
    ])


def _subhead(p: Palette, counts: dict) -> str:
    """One optional, plain line under the tally -- never at the cost of clarity."""
    ok, fail, unv = counts[Verdict.OK], counts[Verdict.FAIL], counts[Verdict.UNVERIFIABLE]
    if fail:
        msg = "None of it holds up." if (ok == 0 and unv == 0) else (
            "Look at the failed line first." if fail == 1 else "Look at the failed lines first.")
    elif unv and ok:
        msg = "The rest checks out."
    elif unv and not ok:
        msg = "Nothing here could be checked this session."
    else:
        msg = "All clear."
    return p.dim(msg)


def _headline(p: Palette, counts: dict) -> str:
    """Back-compat single-string headline (tally + subhead) for legacy callers."""
    return _tally_line(p, counts) + "\n" + _subhead(p, counts)


def render_report(
    findings: list[Finding],
    *,
    show_art: bool = True,
    color: bool | None = None,
    elapsed: float | None = None,
) -> str:
    level = _color_level(sys.stdout) if color is None else (3 if color else 0)
    on = level >= 1
    p = Palette(on)
    out: list[str] = []

    header = _header_block(p, show_art, level)
    if header is not None:
        out.append(header)
        out.append("")

    counts = tally(findings)
    out.append(_tally_line(p, counts))
    out.append(_subhead(p, counts))
    out.append("")

    subj_w = min(max((len(f.display) for f in findings), default=12), 40)
    # Visible columns before the reason: "  NN. " + marker(8) + "  " + subject + "  ".
    reason_indent = 2 + 4 + _MARKER_WIDTH + 2 + subj_w + 2
    reason_w = max(24, _term_width() - reason_indent)

    for i, f in enumerate(findings, start=1):
        marker = _marker(p, f.result.verdict)
        subject = _truncate(f.display, 40).ljust(subj_w)
        lines = textwrap.wrap(" ".join(f.result.detail.split()), reason_w) or [""]
        if len(lines) > 2:  # keep it compact; never cut mid-word
            lines = lines[:2]
            lines[1] = _truncate(lines[1] + " …", reason_w)
        out.append(f"  {p.dim(f'{i:>2}.')} {marker}  {subject}  {p.dim(lines[0])}")
        for cont in lines[1:]:
            out.append(" " * reason_indent + p.dim(cont))

    out.append("")
    hint = p.dim("  run `redpen explain <n>` to see the evidence behind any line")
    if elapsed is not None:
        hint += p.dim(f"   ({elapsed:.1f}s)")
    out.append(hint)
    return "\n".join(out)


def render_explain(record: dict, *, color: bool | None = None) -> str:
    """Full audit trail for one numbered verdict from the last run."""
    import json as _json

    level = _color_level(sys.stdout) if color is None else (3 if color else 0)
    p = Palette(level >= 1)
    verdict = record.get("verdict", "UNVERIFIABLE")
    marker = _marker(p, _VERDICT_BY_NAME.get(verdict, Verdict.UNVERIFIABLE)).rstrip()

    out = [p.bold(f"{TOOL_NAME} — verdict #{record.get('n', '?')}: {marker}"), ""]
    out.append(f"  {p.dim('claim:  ')} {record.get('claim', '')}")
    out.append(f"  {p.dim('subject:')} {record.get('subject', '')}")
    out.append(f"  {p.dim('probe:  ')} {record.get('probe', '')}")
    out.append(f"  {p.dim('reason: ')} {record.get('reason', '')}")
    out.append("")

    commands = record.get("commands") or []
    if commands:
        out.append(p.bold("  commands run:"))
        for c in commands:
            out.append(f"    {p.dim('$')} {c}")
    else:
        out.append(p.dim("  commands run: none (this probe inspects state directly)"))
    out.append("")

    evidence = {k: v for k, v in (record.get("evidence") or {}).items() if k != "commands"}
    out.append(p.bold("  evidence:"))
    for line in _json.dumps(evidence, indent=2, default=str).splitlines():
        out.append(f"    {p.dim(line)}")
    return "\n".join(out)


def render_audit(items: list[dict], *, color: bool | None = None) -> str:
    """Render the full-request audit: each asked item vs. what holds up."""
    if not items:
        return ""
    level = _color_level(sys.stdout) if color is None else (3 if color else 0)
    p = Palette(level >= 1)

    style = {
        "DONE": (p.green_b, "●"),
        "UNSUBSTANTIATED": (p.amber_b, "▲"),
        "SKIPPED": (p.red_b, "●"),
        "UNVERIFIABLE": (p.amber_b, "▲"),
        "UNKNOWN": (p.amber_b, "▲"),
    }
    gaps = sum(1 for i in items if i.get("status") != "DONE")
    if gaps:
        noun = "item" if gaps == 1 else "items"
        head = p.bold(f"Request audit — {gaps} asked-for {noun} unaccounted for:")
    else:
        head = p.bold("Request audit — everything you asked for is accounted for.")
    out = [head, ""]

    width = min(max((len(i.get("item", "")) for i in items), default=12), 40)
    for i in items:
        paint, sym = style.get(i.get("status", "UNKNOWN"), (p.yellow, "·"))
        status = paint(f"{sym} {i.get('status', 'UNKNOWN'):<15}")
        item = _truncate(i.get("item", ""), 40).ljust(width)
        note = p.dim(_truncate(i.get("note", ""), 44))
        out.append(f"  {status}  {item}  {note}")

    done = sum(1 for i in items if i.get("status") == "DONE")
    unsub = sum(1 for i in items if i.get("status") == "UNSUBSTANTIATED")
    skip = sum(1 for i in items if i.get("status") == "SKIPPED")
    out.append("")
    out.append(
        "  "
        + " · ".join(
            [p.green_b(f"{done} done"), p.amber_b(f"{unsub} unsubstantiated"), p.red_b(f"{skip} skipped")]
        )
    )
    return "\n".join(out)


def render_history(rows: list[HistoryRow], *, color: bool | None = None) -> str:
    level = _color_level(sys.stdout) if color is None else (3 if color else 0)
    p = Palette(level >= 1)
    if not rows:
        return p.dim("Nothing in the ledger yet — run `redpen check` to start recording verdicts.")

    out = [p.bold(f"{TOOL_NAME} ledger — most recent first:"), ""]
    for r in rows:
        marker = _marker(p, _VERDICT_BY_NAME.get(r.verdict, Verdict.UNVERIFIABLE))
        ts = p.dim(r.ts.replace("T", " ").replace("+00:00", "Z"))
        subject = _truncate(r.claim, 40).ljust(40)
        detail = p.dim(_truncate(r.detail or "", 40))
        out.append(f"  {marker} {ts}  {subject}  {detail}")
    return "\n".join(out)

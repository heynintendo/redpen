"""Verdict renderer: compact, fast, terminal-aware.

Leads with a plain tally (verified / can't confirm / failed), one line per
finding (marker + subject + a readable reason), and a header mascot that degrades
by terminal capability: full truecolor pixel art on truecolor TTYs, a 256-color
version on 256-color terminals, a small clean ASCII mascot as the last resort.
"""

from __future__ import annotations

import os
import re
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


def _term_width() -> int:
    return min(max(shutil.get_terminal_size((100, 24)).columns, 40), 120)


def _cap(s: str) -> str:
    """Collapse whitespace and capitalize the first letter for sentence feel.

    Leaves the first token alone when it is a path, filename, command, or other
    code token (contains ``.`` / ``/`` / ``\\`` or is backtick-quoted), so
    "config.py does not exist" and "`pytest` ran" are never mangled. Never
    truncates.
    """
    s = " ".join(s.split())
    if not s:
        return s
    first = s.split(None, 1)[0]
    if first[:1].isalpha() and not any(c in first for c in "./\\`"):
        return s[0].upper() + s[1:]
    return s


def _humanize_reason(detail: str) -> str:
    """Render a probe's reason as a complete, plain sentence. Presentation only:
    collapse whitespace, turn an ' — ' clause break into a sentence break,
    capitalize each sentence (leaving code tokens intact), and end with a period
    when the text ends in a word, not after quoted evidence. Never truncates."""
    s = " ".join((detail or "").split())
    if not s:
        return ""
    s = re.sub(r"\s+[—–]\s+", ". ", s)  # an em/en-dash clause break becomes a sentence
    parts = [p for p in re.split(r"(?<=[.!?])\s+", s) if p]
    s = " ".join(_cap(p) for p in parts)
    if s and s[-1].isalpha():
        s += "."
    return s


def _vis_pad(s: str, width: int) -> str:
    """Left-justify visible text to ``width`` (``s`` carries no ANSI here)."""
    return s + " " * max(0, width - len(s))


def _truncate(s: str, n: int) -> str:
    """Clip with an ellipsis. Used only by the compact `redpen history` log, never
    by the verdict table or the request audit (those wrap, never truncate)."""
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _columns_table(p: Palette, rows: list[tuple[str, str, str]], term_w: int,
                   *, marker_w: int, numbered: bool) -> list[str]:
    """An aligned, wrapping, never-truncated table.

    Each row is ``(marker, left, right)`` where ``marker`` is the pre-colored,
    visible-width-``marker_w`` status. Left (the claim/item) and right (the
    reason/note) wrap within their columns and stay aligned. On a narrow terminal
    the right column drops to its own indented full-width line beneath the row,
    so text is wrapped, never squeezed into nonsense or clipped.
    """
    out: list[str] = []
    gutter = "  "
    num_w = 3 if numbered else 0          # "{i:>2}."
    num_gap = 1 if numbered else 0
    prefix = len(gutter) + num_w + num_gap + marker_w + 2   # columns before `left`
    avail = max(20, term_w - prefix - 2)                    # left + 2 + right
    left_w = min(40, max(14, avail // 2))
    right_w = avail - left_w
    beneath = right_w < 28

    def num_cell(i: int, k: int) -> str:
        if not numbered:
            return ""
        return p.dim(f"{i:>2}." if k == 0 else "   ") + " "

    for i, (marker, left, right) in enumerate(rows, start=1):
        left_lines = textwrap.wrap(left, max(left_w, avail) if beneath else left_w) or [""]
        if beneath:
            for k, cl in enumerate(left_lines):
                stat = marker if k == 0 else " " * marker_w
                out.append(f"{gutter}{num_cell(i, k)}{stat}  {cl}".rstrip())
            for rl in textwrap.wrap(right, max(24, term_w - prefix)):
                out.append(" " * prefix + p.dim(rl))
        else:
            right_lines = textwrap.wrap(right, right_w) or [""]
            for k in range(max(len(left_lines), len(right_lines))):
                stat = marker if k == 0 else " " * marker_w
                cl = _vis_pad(left_lines[k] if k < len(left_lines) else "", left_w)
                rl = right_lines[k] if k < len(right_lines) else ""
                out.append(f"{gutter}{num_cell(i, k)}{stat}  {cl}  {p.dim(rl) if rl else ''}".rstrip())
    return out


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

    rows = [(_marker(p, f.result.verdict), _cap(f.display), _humanize_reason(f.result.detail))
            for f in findings]
    out.extend(_columns_table(p, rows, _term_width(), marker_w=_MARKER_WIDTH, numbered=True))

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

    # marker = "<sym> <STATUS>" padded to a fixed visible width so columns stack.
    status_w = max(len(i.get("status", "UNKNOWN")) for i in items)
    marker_w = 1 + 1 + status_w  # sym + space + status word
    rows = []
    for i in items:
        paint, sym = style.get(i.get("status", "UNKNOWN"), (p.yellow, "·"))
        marker = paint(f"{sym} {i.get('status', 'UNKNOWN'):<{status_w}}")
        rows.append((marker, _cap(i.get("item", "")), _humanize_reason(i.get("note", ""))))
    out.extend(_columns_table(p, rows, _term_width(), marker_w=marker_w, numbered=False))

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

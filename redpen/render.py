"""Verdict renderer: compact, fast, ANSI-colored.

One line per finding -- symbol + subject + a one-line piece of evidence -- with
a personality headline and a summary footer. The examiner is terse and exacting.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .config import TOOL_NAME
from .engine import Finding, tally
from .ledger import HistoryRow
from .probes.base import Verdict

_CSI = "\033["

# A verdict marker is a glyph + a short word. Color carries OK vs FAIL (same dot
# shape), and the word label carries it when color is off (colorblind/NO_COLOR);
# UNSURE uses a distinct hazard-triangle shape. Colored, each is bright + bold;
# with color off we fall back to plain bracket labels. Markers pad to one width.
_MARKERS = {
    Verdict.OK: ("●", "OK", "[OK]", "green_b"),
    Verdict.FAIL: ("●", "FAIL", "[FAIL]", "red_b"),
    Verdict.UNVERIFIABLE: ("▲", "UNSURE", "[??]", "amber_b"),
}
_MARKER_WIDTH = 8  # visible width; "▲ UNSURE" is the widest

# String-keyed view for callers that only have the verdict name.
_VERDICT_BY_NAME = {v.value: v for v in Verdict}


def _marker(p: Palette, verdict: Verdict) -> str:
    """A fixed-width verdict marker: bright bold "● OK" / "● FAIL" / "▲ UNSURE",
    or plain "[OK]" / "[FAIL]" / "[??]" with color off."""
    glyph, word, plain, paint = _MARKERS[verdict]
    if not p.on:
        return plain.ljust(_MARKER_WIDTH)
    text = f"{glyph} {word}"
    pad = " " * max(0, _MARKER_WIDTH - len(text))  # uncolored padding aligns columns
    return getattr(p, paint)(text) + pad


def _supports_color(stream) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return bool(getattr(stream, "isatty", lambda: False)())


class Palette:
    """Wraps text in ANSI codes, or doesn't, depending on ``on``."""

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

    # Bright + bold, standard ANSI (90-series) so verdict markers render
    # distinctly on every terminal, not just truecolor ones.
    def green_b(self, s):
        return self._w("1;92", s)

    def red_b(self, s):
        return self._w("1;91", s)

    def amber_b(self, s):
        return self._w("1;93", s)


def _truncate(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


# The header mascot is truecolor ANSI half-block art shipped at
# docs/mascot_ansi.txt (and mirrored into the package for wheel installs).
_ART_CANDIDATES = (
    Path(__file__).resolve().parent.parent / "docs" / "mascot_ansi.txt",
    Path(__file__).resolve().parent / "_assets" / "mascot_ansi.txt",
)


def load_mascot_art() -> str | None:
    """Read the truecolor ANSI mascot, or None if it can't be found/read."""
    for path in _ART_CANDIDATES:
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8").rstrip("\n")
        except OSError:
            continue
    return None


def _header_block(p: Palette, show_art: bool, on: bool) -> str | None:
    """The header above a report.

    Returns the rich ANSI mascot only when art is wanted AND color is on (which
    already accounts for --no-color, NO_COLOR and a non-TTY stdout). Otherwise a
    one-line text title. Returns None when --no-art is set (no header at all).
    """
    if not show_art:
        return None
    if on:
        art = load_mascot_art()
        if art:
            return art
    return p.bold(f"{TOOL_NAME} —")


def render_header(show_art: bool = True, color: bool | None = None) -> str:
    """Public header for callers outside the report (e.g. the no-claims path)."""
    on = _supports_color(sys.stdout) if color is None else color
    block = _header_block(Palette(on), show_art, on)
    return block or ""


def _headline(p: Palette, counts: dict) -> str:
    ok, fail, unv = counts[Verdict.OK], counts[Verdict.FAIL], counts[Verdict.UNVERIFIABLE]
    if fail:
        if fail == 1:
            return p.bold("Marked. One claim doesn't hold up:")
        if ok == 0 and unv == 0:
            return p.bold(f"Marked. None of these {fail} hold up:")
        return p.bold(f"Marked. {fail} of these claims don't hold up:")
    if unv and ok:
        thing = "one thing" if unv == 1 else f"{unv} things"
        return p.bold(f"Marked. The rest holds up, but there's {thing} I can't confirm.")
    if unv and not ok:
        return p.bold(
            "Can't confirm any of these. There's nothing in the session that proves "
            "them either way — not wrong, just unverified."
        )
    return p.bold("Marked. Everything checks out. Don't get used to it.")


def _footer(p: Palette, counts: dict, elapsed: float | None) -> str:
    ok, fail, unv = counts[Verdict.OK], counts[Verdict.FAIL], counts[Verdict.UNVERIFIABLE]
    parts = " · ".join(
        [p.green_b(f"{ok} OK"), p.red_b(f"{fail} FAIL"), p.amber_b(f"{unv} unsure")]
    )
    foot = "  " + parts
    if elapsed is not None:
        foot += p.dim(f"   ({elapsed:.1f}s)")
    return foot


def render_report(
    findings: list[Finding],
    *,
    show_art: bool = True,
    color: bool | None = None,
    elapsed: float | None = None,
) -> str:
    on = _supports_color(sys.stdout) if color is None else color
    p = Palette(on)
    out: list[str] = []

    header = _header_block(p, show_art, on)
    if header is not None:
        out.append(header)
        out.append("")

    counts = tally(findings)
    out.append(_headline(p, counts))
    out.append("")

    width = min(max((len(f.display) for f in findings), default=12), 40)
    for i, f in enumerate(findings, start=1):
        marker = _marker(p, f.result.verdict)
        subject = _truncate(f.display, 40).ljust(width)
        evidence = p.dim(_truncate(f.result.detail, 60))
        out.append(f"  {p.dim(f'{i:>2}.')} {marker}  {subject}  {evidence}")

    out.append("")
    out.append(_footer(p, counts, elapsed))
    out.append(p.dim("  run `redpen explain <n>` to see the evidence behind any line"))
    return "\n".join(out)


def render_explain(record: dict, *, color: bool | None = None) -> str:
    """Full audit trail for one numbered verdict from the last run."""
    import json as _json

    on = _supports_color(sys.stdout) if color is None else color
    p = Palette(on)
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
    on = _supports_color(sys.stdout) if color is None else color
    p = Palette(on)

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
    on = _supports_color(sys.stdout) if color is None else color
    p = Palette(on)
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

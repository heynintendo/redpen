"""Deterministic fake `claude` and `gh` executables, dropped into a per-case bin
dir and put first on PATH so they shadow the real tools.

The fake `claude` speaks the exact protocol redpen.judge expects: it emits a
`claude --output-format json` envelope ``{"type":"result","is_error":...,
"result":"<reply>"}`` where <reply> is itself JSON (a verdict object, or an audit
/ decompose array). Its behavior is driven entirely by env vars so each case is
reproducible and spends zero real quota:

    REDPEN_FAKE_MODE      ok | fail | timeout | garbage | error_envelope | bad_verdict
    REDPEN_FAKE_VERDICT   OK | FAIL | UNVERIFIABLE   (for judge prompts)
    REDPEN_FAKE_AUDIT     a JSON array string        (for the request audit)
    REDPEN_FAKE_DECOMPOSE a JSON array string        (for decompose prompts)
    REDPEN_FAKE_LOG       path: each call appends {"argv","prompt"} as a line
    REDPEN_FAKE_SLEEP     seconds to sleep in timeout mode

The fake `gh` covers `gh auth status` and `gh pr view --json ...`, driven by
REDPEN_FAKE_GH (authed|unauthed) and REDPEN_FAKE_GH_PR (a JSON PR object).
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

_FAKE_CLAUDE = r'''#!__PYTHON__
import json, os, sys, time

def _log(prompt):
    p = os.environ.get("REDPEN_FAKE_LOG")
    if not p:
        return
    try:
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"argv": sys.argv[1:], "prompt": prompt}) + "\n")
    except OSError:
        pass

def _prompt(argv):
    if "-p" in argv:
        i = argv.index("-p")
        if i + 1 < len(argv):
            return argv[i + 1]
    return ""

prompt = _prompt(sys.argv)
_log(prompt)
mode = os.environ.get("REDPEN_FAKE_MODE", "ok")

if mode == "fail":
    sys.stderr.write("simulated claude failure\n")
    sys.exit(7)
if mode == "timeout":
    time.sleep(float(os.environ.get("REDPEN_FAKE_SLEEP", "60")))
    sys.exit(0)
if mode == "garbage":
    sys.stdout.write("this is not json at all, just prose from a confused model\n")
    sys.exit(0)

is_error = (mode == "error_envelope")

if "You are RedPen's verifier" in prompt:
    if mode == "bad_verdict":
        reply = json.dumps({"verdict": "MAYBE", "reason": "not a real verdict"})
    else:
        reply = json.dumps({
            "verdict": os.environ.get("REDPEN_FAKE_VERDICT", "OK"),
            "reason": os.environ.get("REDPEN_FAKE_REASON", "mock verdict"),
        })
elif "You audit whether a coding request" in prompt:
    aud = os.environ.get("REDPEN_FAKE_AUDIT", "[]")
    if aud == "echo":
        import re as _re
        m = _re.search(r"USER REQUEST:\n(.*?)\n\nASSISTANT CLAIMED:", prompt, _re.DOTALL)
        req = (m.group(1).strip()[:60] if m else "NO-REQUEST")
        reply = json.dumps([{"item": req, "status": "UNVERIFIABLE", "note": "echoed-request"}])
    else:
        reply = aud
elif "Break the user's request" in prompt:
    reply = os.environ.get("REDPEN_FAKE_DECOMPOSE", "[]")
else:
    reply = json.dumps({"verdict": "UNVERIFIABLE", "reason": "unrecognized prompt"})

sys.stdout.write(json.dumps({"type": "result", "is_error": is_error, "result": reply}))
sys.exit(0)
'''

_FAKE_GH = r'''#!__PYTHON__
import os, sys
argv = sys.argv[1:]
state = os.environ.get("REDPEN_FAKE_GH", "authed")
if argv[:2] == ["auth", "status"]:
    sys.exit(0 if state == "authed" else 1)
if argv[:2] == ["pr", "view"]:
    pr = os.environ.get("REDPEN_FAKE_GH_PR", "")
    if not pr:
        sys.stderr.write("no pull requests found for this branch\n")
        sys.exit(1)
    sys.stdout.write(pr)
    sys.exit(0)
sys.exit(0)
'''


def _write_exe(path: Path, body: str) -> Path:
    path.write_text(body.replace("__PYTHON__", sys.executable), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def make_bin_dir(workspace, *, claude: bool = False, gh: bool = False) -> Path:
    """Create workspace/_bin containing the requested fake executables."""
    bin_dir = Path(workspace) / "_bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    if claude:
        _write_exe(bin_dir / "claude", _FAKE_CLAUDE)
    if gh:
        _write_exe(bin_dir / "gh", _FAKE_GH)
    return bin_dir


def controlled_path(bin_dir=None) -> str:
    """A minimal, deterministic PATH: optional fake-bin dir, then git/coreutils.

    /usr/bin holds git on this platform; gh and claude live elsewhere, so they
    are absent unless a fake is dropped into ``bin_dir``.
    """
    parts = []
    if bin_dir is not None:
        parts.append(str(bin_dir))
    parts += ["/usr/bin", "/bin", os.path.dirname(sys.executable)]
    return os.pathsep.join(parts)

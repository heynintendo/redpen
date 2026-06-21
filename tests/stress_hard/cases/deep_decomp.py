"""--deep adversarial cases: the LLM judge seam and the request audit.

The judge subprocess is mocked by a deterministic fake `claude` on PATH (see
harness/fake_bins.py), so these run the REAL plumbing (subprocess, envelope
parsing, evidence-hash cache, audit decomposition) without spending quota. We
assert RedPen's responsibilities -- which are deterministic -- not the model's
judgement quality:

  * deterministic OK/FAIL is NEVER sent to the judge (precision gate);
  * an UNVERIFIABLE result flows through the judge's verdict (the seam);
  * any judge failure/timeout/garbage/unknown verdict falls back to
    UNVERIFIABLE -- never a crash, never a guessed FAIL;
  * the audit reconciles the FINAL user request (evolving/cancelled asks),
    normalizes unknown statuses to UNVERIFIABLE, and never invents items.

A tiny --live subset (off by default) exercises the real `claude` and lives in
test_live.py.
"""

from __future__ import annotations

import json

from harness.builders import TB, commit_all, make_repo, write_file
from harness.fake_bins import controlled_path, make_bin_dir
from harness.model import FAIL, OK, UNV, Built, Case, ef

AXIS = "deep_decomp"


def _deep(ws, *, final, user, env_extra=None, files=None, writes=None, bash=None,
          pre_user=None, claude=True, commit=True):
    root = make_repo(ws / "repo", files or {"README.md": "# x\n"}, commit=commit)
    bind = make_bin_dir(ws, claude=claude)
    t = TB(cwd=root)
    for u in (pre_user or []):
        t.user(u)
    t.user(user)
    for rel in (writes or []):
        t.write(rel)
    for ev in (bash or []):
        t.bash(ev["cmd"], output=ev.get("out", ""), failed=ev.get("failed", False))
    t.assistant(final)
    env = {"PATH": controlled_path(bind), "REDPEN_LLM_MODEL": "mock"}
    env.update(env_extra or {})
    return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"), extra_args=("--deep",), env=env)


def _c(cid, title, build, efs, *, invariant=None, tags=()):
    return Case(f"{AXIS}/{cid}", AXIS, title, build, efs, deep=True, invariant=invariant,
               tags=tags)


# ---- audit invariants (read straight from the JSON request_audit) -----------
def _audit_final_request(d, rc, err):
    aud = d.get("request_audit") or []
    if not aud:
        return "no request_audit produced for the final request"
    item = aud[0].get("item", "")
    if "NEW-ASK" not in item:
        return f"audit did not reconcile the FINAL request: {item!r}"
    if "OLD-ASK" in item:
        return f"audit leaked a superseded request: {item!r}"
    return ""


def _audit_statuses(expected):
    def check(d, rc, err):
        got = [a.get("status") for a in (d.get("request_audit") or [])]
        return "" if got == expected else f"audit statuses {got} != expected {expected}"
    return check


def _audit_len(n):
    def check(d, rc, err):
        aud = d.get("request_audit") or []
        return "" if len(aud) == n else f"audit had {len(aud)} items, expected {n} (no invention/drop)"
    return check


def cases():
    out = []
    c = out.append

    # ---- precision gate ---------------------------------------------------
    c(_c("precision_det_fail_kept",
         "deterministic FAIL is never overridden by a judge saying OK",
         lambda ws, rng: _deep(ws, user="make the tests pass", final="All tests pass now.",
                               bash=[{"cmd": "pytest -q", "out": "=== 1 failed in 0.1s ===", "failed": True}],
                               env_extra={"REDPEN_FAKE_VERDICT": "OK"}),
         [ef("tests_pass", true=False, accept={FAIL},
             note="judge must not see (or flip) a deterministic FAIL")]))

    c(_c("precision_det_ok_kept",
         "deterministic OK is never overridden by a judge saying FAIL",
         lambda ws, rng: _deep(ws, user="create app.py", final="Created app.py.",
                               files={"README.md": "# x\n", "app.py": "print(1)\n"},
                               writes=["app.py"], env_extra={"REDPEN_FAKE_VERDICT": "FAIL"}),
         [ef("file_present", true=True, accept={OK}, subject="app.py")]))

    # ---- the seam: UNVERIFIABLE flows through the judge's verdict ----------
    c(_c("seam_unmapped_to_ok",
         "UNVERIFIABLE 'unmapped' refined to OK by the judge",
         lambda ws, rng: _deep(ws, user="refactor the parser", final="Refactored the parser internals.",
                               env_extra={"REDPEN_FAKE_VERDICT": "OK", "REDPEN_FAKE_AUDIT": "[]"}),
         [ef("unmapped", true=True, accept={OK}, note="seam: judge upgrades UNVERIFIABLE")]))

    c(_c("seam_unmapped_to_fail",
         "UNVERIFIABLE 'unmapped' refined to FAIL by the judge (a real lie)",
         lambda ws, rng: _deep(ws, user="delete the legacy module", final="Deleted the legacy module entirely.",
                               env_extra={"REDPEN_FAKE_VERDICT": "FAIL", "REDPEN_FAKE_AUDIT": "[]"}),
         [ef("unmapped", true=False, accept={FAIL})]))

    # ---- judge failure modes -> UNVERIFIABLE fallback, never a guessed FAIL --
    for mode in ["fail", "garbage", "error_envelope", "bad_verdict"]:
        c(_c(f"fallback_{mode}",
             f"judge mode={mode} -> UNVERIFIABLE fallback (no crash, no guessed FAIL)",
             (lambda m: lambda ws, rng: _deep(
                 ws, user="refactor the parser", final="Refactored the parser internals.",
                 env_extra={"REDPEN_FAKE_MODE": m, "REDPEN_FAKE_AUDIT": "[]"}))(mode),
             [ef("unmapped", true=None, accept={UNV},
                 note="degrade to UNVERIFIABLE; a FAIL here would be a guessed FAIL")],
             tags=("fallback",)))

    # ---- claude unavailable (no fake on PATH) -> graceful degradation ------
    c(_c("claude_unavailable",
         "--deep with no claude on PATH -> UNVERIFIABLE, audit empty, no hang/FAIL",
         lambda ws, rng: _deep(ws, user="refactor the parser", final="Refactored the parser internals.",
                               claude=False),
         [ef("unmapped", true=None, accept={UNV})],
         invariant=lambda d, rc, err: "" if (d.get("request_audit") == []) else "audit should be empty without claude",
         tags=("offline",)))

    # ---- audit: reconcile the FINAL request (evolving ask) ----------------
    c(_c("audit_evolving_request",
         "audit reconciles the FINAL user request, not a superseded one",
         lambda ws, rng: _deep(
             ws, pre_user=["OLD-ASK: build a whole billing dashboard"],
             user="NEW-ASK: actually scrap that, just fix the date typo",
             final="Refactored the parser internals.",
             env_extra={"REDPEN_FAKE_VERDICT": "UNVERIFIABLE", "REDPEN_FAKE_AUDIT": "echo"}),
         [ef("unmapped", true=None, accept={UNV})],
         invariant=_audit_final_request, tags=("audit",)))

    c(_c("audit_cancelled_request",
         "audit uses the final (cancellation) turn",
         lambda ws, rng: _deep(
             ws, pre_user=["OLD-ASK: add OAuth login"],
             user="NEW-ASK: never mind, cancel that entirely",
             final="Refactored the parser internals.",
             env_extra={"REDPEN_FAKE_VERDICT": "UNVERIFIABLE", "REDPEN_FAKE_AUDIT": "echo"}),
         [ef("unmapped", true=None, accept={UNV})],
         invariant=_audit_final_request, tags=("audit",)))

    # ---- audit: unknown statuses normalize to UNVERIFIABLE -----------------
    norm_audit = json.dumps([
        {"item": "create README", "status": "DONE", "note": "present"},
        {"item": "push", "status": "skipped", "note": "no upstream"},
        {"item": "tests", "status": "weird", "note": "?"},
    ])
    c(_c("audit_status_normalization",
         "unknown audit status -> UNVERIFIABLE (never a guessed SKIPPED)",
         lambda ws, rng: _deep(ws, user="do three things", final="Refactored the parser internals.",
                               env_extra={"REDPEN_FAKE_VERDICT": "UNVERIFIABLE", "REDPEN_FAKE_AUDIT": norm_audit}),
         [ef("unmapped", true=None, accept={UNV})],
         invariant=_audit_statuses(["DONE", "SKIPPED", "UNVERIFIABLE"]), tags=("audit",)))

    # ---- audit: RedPen never invents/drops items (passthrough of N) --------
    two_audit = json.dumps([
        {"item": "thing one", "status": "DONE", "note": "a"},
        {"item": "thing two", "status": "UNSUBSTANTIATED", "note": "b"},
    ])
    c(_c("audit_no_invention",
         "audit passes the model's items through without inventing SKIPPED",
         lambda ws, rng: _deep(ws, user="do two things", final="Refactored the parser internals.",
                               env_extra={"REDPEN_FAKE_VERDICT": "UNVERIFIABLE", "REDPEN_FAKE_AUDIT": two_audit}),
         [ef("unmapped", true=None, accept={UNV})],
         invariant=_audit_len(2), tags=("audit",)))

    # ---- audit: huge 15-item request -> no crash, audit present -----------
    huge_req = "Please: " + "; ".join(f"item {i}" for i in range(1, 16))
    c(_c("audit_huge_request",
         "15+ sub-item request audited without crashing",
         lambda ws, rng: _deep(ws, user=huge_req, final="Refactored the parser internals.",
                               env_extra={"REDPEN_FAKE_VERDICT": "UNVERIFIABLE", "REDPEN_FAKE_AUDIT": "echo"}),
         [ef("unmapped", true=None, accept={UNV})],
         invariant=lambda d, rc, err: "" if (d.get("request_audit")) else "expected a non-empty audit",
         tags=("audit",)))

    return out

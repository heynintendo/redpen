"""Tests for the cheap probe-pack: dep_present, typecheck_clean, test_count, symbol_exists."""

from __future__ import annotations

import redpen.probes.pack_probes as pack
from redpen.changeset import ChangedSet, normalize
from redpen.claims import extract_claims
from redpen.probes import dep_present, symbol_exists, typecheck_clean
from redpen.probes.base import ProbeContext, Verdict
from redpen.transcript import ToolEvent, Transcript

# test_count is called as pack.test_count below: a bare module-level `test_count`
# would be collected by pytest as a test (it matches the `test*` pattern).


# --- dep_present ------------------------------------------------------------


def test_dep_present_in_manifest_and_lock_is_ok(tmp_path):
    (tmp_path / "package.json").write_text('{"dependencies": {"left-pad": "^1.0.0"}}')
    (tmp_path / "package-lock.json").write_text('{"packages": {"node_modules/left-pad": {"version": "1.0.0"}}}')
    res = dep_present(ProbeContext(cwd=tmp_path), name="left-pad")
    assert res.verdict is Verdict.OK


def test_dep_in_manifest_only_is_unverifiable(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = ["requests>=2.0"]\n')
    res = dep_present(ProbeContext(cwd=tmp_path), name="requests")
    assert res.verdict is Verdict.UNVERIFIABLE
    assert res.evidence["in_manifest"] is True and res.evidence["in_lockfile"] is False


def test_dep_absent_everywhere_is_fail(tmp_path):
    (tmp_path / "package.json").write_text('{"dependencies": {"react": "18"}}')
    (tmp_path / "package-lock.json").write_text('{"packages": {}}')
    res = dep_present(ProbeContext(cwd=tmp_path), name="nonexistent-pkg")
    assert res.verdict is Verdict.FAIL


def test_cargo_dep_in_manifest_and_lock_ok(tmp_path):
    (tmp_path / "Cargo.toml").write_text('[dependencies]\nserde = "1.0"\n')
    (tmp_path / "Cargo.lock").write_text('[[package]]\nname = "serde"\nversion = "1.0.1"\n')
    res = dep_present(ProbeContext(cwd=tmp_path), name="serde")
    assert res.verdict is Verdict.OK


# --- typecheck_clean --------------------------------------------------------


def test_typecheck_no_tool_is_unverifiable(tmp_path):
    assert typecheck_clean(ProbeContext(cwd=tmp_path)).verdict is Verdict.UNVERIFIABLE


def test_typecheck_clean_run_is_ok(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\n")
    t = Transcript(tool_events=[ToolEvent(tool="Bash", label="mypy .", command="mypy .", failed=False,
                                          output="Success: no issues found in 5 source files")])
    res = typecheck_clean(ProbeContext(cwd=tmp_path, transcript=t))
    assert res.verdict is Verdict.OK


def test_typecheck_errors_is_fail(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\n")
    t = Transcript(tool_events=[ToolEvent(tool="Bash", label="mypy .", command="mypy .", failed=True,
                                          output="app.py:3: error: Incompatible types\nFound 1 error in 1 file")])
    res = typecheck_clean(ProbeContext(cwd=tmp_path, transcript=t))
    assert res.verdict is Verdict.FAIL


def test_typecheck_configured_but_not_run_is_unverifiable(tmp_path):
    (tmp_path / "tsconfig.json").write_text("{}")
    res = typecheck_clean(ProbeContext(cwd=tmp_path, transcript=Transcript(tool_events=[])))
    assert res.verdict is Verdict.UNVERIFIABLE


# --- test_count -------------------------------------------------------------


def test_count_matches_is_ok():
    t = Transcript(tool_events=[ToolEvent(tool="Bash", label="pytest", command="pytest", failed=False,
                                          output="===== 42 passed in 1.2s =====")])
    res = pack.test_count(ProbeContext(cwd=".", transcript=t), n=42)
    assert res.verdict is Verdict.OK


def test_count_mismatch_is_fail():
    t = Transcript(tool_events=[ToolEvent(tool="Bash", label="pytest", command="pytest", failed=False,
                                          output="===== 7 passed in 0.2s =====")])
    res = pack.test_count(ProbeContext(cwd=".", transcript=t), n=42)
    assert res.verdict is Verdict.FAIL
    assert res.evidence["actual"] == 7


def test_count_no_run_is_unverifiable():
    res = pack.test_count(ProbeContext(cwd=".", transcript=Transcript(tool_events=[])), n=42)
    assert res.verdict is Verdict.UNVERIFIABLE


def test_count_with_failures_is_fail():
    t = Transcript(tool_events=[ToolEvent(tool="Bash", label="pytest", command="pytest", failed=False,
                                          output="=== 1 failed, 41 passed ===")])
    res = pack.test_count(ProbeContext(cwd=".", transcript=t), n=42)
    assert res.verdict is Verdict.FAIL


# --- symbol_exists ----------------------------------------------------------


def _cs(cwd, *files):
    cs = ChangedSet()
    for rel in files:
        cs.paths[normalize(cwd, rel)] = {"transcript"}
    return cs


def test_symbol_found_in_changed_file_is_ok(tmp_path):
    (tmp_path / "auth.py").write_text("def authenticate(user):\n    return True\n")
    ctx = ProbeContext(cwd=tmp_path, changed_set=_cs(tmp_path, "auth.py"))
    res = symbol_exists(ctx, symbol="authenticate")
    assert res.verdict is Verdict.OK


def test_symbol_not_found_is_unverifiable_not_fail(tmp_path):
    (tmp_path / "auth.py").write_text("def login(user):\n    return True\n")
    ctx = ProbeContext(cwd=tmp_path, changed_set=_cs(tmp_path, "auth.py"))
    res = symbol_exists(ctx, symbol="authenticate")
    assert res.verdict is Verdict.UNVERIFIABLE  # might be elsewhere -> never FAIL


def test_symbol_no_changeset_is_unverifiable(tmp_path):
    res = symbol_exists(ProbeContext(cwd=tmp_path), symbol="foo")
    assert res.verdict is Verdict.UNVERIFIABLE


# --- claim extraction wires the pack probes ---------------------------------


def test_extractor_maps_pack_claims():
    claims = extract_claims(
        "I added dependency requests. I added function authenticate. All 42 tests pass. mypy is clean.",
        source="adhoc",
    )
    names = {spec.name for c in claims for spec in c.probe_specs}
    assert {"dep_present", "symbol_exists", "test_count", "typecheck_clean"} <= names

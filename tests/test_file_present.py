"""Behavioural tests for the file_present probe.

file_present substantiates a "created/wrote <path>" claim. The success
condition (per spec) is: the named file exists AND is non-empty. A missing
file contradicts the claim (FAIL). An empty file also contradicts a
"wrote"/"created content" claim (FAIL, distinct detail). We never emit FAIL
for absent evidence -- but here a missing file *is* contradicting evidence.
"""

from __future__ import annotations

from redpen.probes import file_present
from redpen.probes.base import ProbeContext, Verdict


def test_present_nonempty_file_is_ok(tmp_path):
    f = tmp_path / "README.md"
    f.write_text("# hello\n")

    res = file_present(ProbeContext(cwd=tmp_path), path="README.md")

    assert res.verdict is Verdict.OK
    assert "README.md" in res.detail
    # Evidence is what a Phase 2 judge would later see -- it must be structured.
    assert res.evidence["exists"] is True
    assert res.evidence["size"] > 0
    assert "mtime" in res.evidence


def test_missing_file_is_fail(tmp_path):
    res = file_present(ProbeContext(cwd=tmp_path), path="nope.txt")

    assert res.verdict is Verdict.FAIL
    assert res.evidence["exists"] is False


def test_empty_file_is_fail_with_distinct_detail(tmp_path):
    f = tmp_path / "empty.py"
    f.write_text("")

    res = file_present(ProbeContext(cwd=tmp_path), path="empty.py")

    assert res.verdict is Verdict.FAIL
    assert "empty" in res.detail.lower()
    assert res.evidence["size"] == 0


def test_relative_path_resolves_against_cwd(tmp_path):
    sub = tmp_path / "src"
    sub.mkdir()
    (sub / "main.py").write_text("print('hi')\n")

    res = file_present(ProbeContext(cwd=tmp_path), path="src/main.py")

    assert res.verdict is Verdict.OK


def test_probe_name_is_reported(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    res = file_present(ProbeContext(cwd=tmp_path), path="a.txt")
    assert res.probe == "file_present"

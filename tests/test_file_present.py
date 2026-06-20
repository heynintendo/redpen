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


def test_empty_file_is_unverifiable_not_fail(tmp_path):
    # An empty file EXISTS, so it doesn't contradict "created <file>".
    # Ambiguous -> UNVERIFIABLE (precision rule), never FAIL.
    f = tmp_path / "empty.py"
    f.write_text("")

    res = file_present(ProbeContext(cwd=tmp_path), path="empty.py")

    assert res.verdict is Verdict.UNVERIFIABLE
    assert "empty" in res.detail.lower()
    assert res.evidence["size"] == 0


def test_whitespace_only_file_is_unverifiable(tmp_path):
    f = tmp_path / "blank.py"
    f.write_text("   \n\t\n")

    res = file_present(ProbeContext(cwd=tmp_path), path="blank.py")

    assert res.verdict is Verdict.UNVERIFIABLE
    assert "whitespace" in res.detail.lower()


def test_absolute_path_is_used_as_is(tmp_path):
    f = tmp_path / "abs.txt"
    f.write_text("content")

    res = file_present(ProbeContext(cwd=tmp_path / "elsewhere"), path=str(f))

    assert res.verdict is Verdict.OK


def test_directory_is_ok_when_non_empty_and_noted(tmp_path):
    d = tmp_path / "pkg"
    d.mkdir()
    (d / "x.py").write_text("x")

    res = file_present(ProbeContext(cwd=tmp_path), path="pkg")

    assert res.verdict is Verdict.OK
    assert res.evidence["is_dir"] is True


def test_empty_directory_is_unverifiable(tmp_path):
    (tmp_path / "empty_pkg").mkdir()

    res = file_present(ProbeContext(cwd=tmp_path), path="empty_pkg")

    assert res.verdict is Verdict.UNVERIFIABLE
    assert res.evidence["is_dir"] is True


def test_broken_symlink_is_unverifiable_not_fail(tmp_path):
    link = tmp_path / "link.txt"
    link.symlink_to(tmp_path / "missing-target.txt")  # target does not exist

    res = file_present(ProbeContext(cwd=tmp_path), path="link.txt")

    assert res.verdict is Verdict.UNVERIFIABLE
    assert res.evidence["symlink"] is True


def test_symlink_to_real_file_is_ok(tmp_path):
    target = tmp_path / "real.txt"
    target.write_text("hello")
    link = tmp_path / "link.txt"
    link.symlink_to(target)

    res = file_present(ProbeContext(cwd=tmp_path), path="link.txt")

    assert res.verdict is Verdict.OK


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

"""Regression tests for two real-world bugs found dogfooding on live sessions.

BUG 1: discovery missed a session launched from a parent dir while the agent
       worked in a child dir (transcript filed under the launch dir).
BUG 2: the extractor dropped a plain completion claim -- "Created
       ~/sorting-algorithms/ with three files" + "all three run correctly".
"""

from __future__ import annotations

import json
from pathlib import Path

from redpen.changeset import build_changed_set
from redpen.claims import claims_from_transcript, extract_claims
from redpen.patterns import created_paths
from redpen.probes import file_present
from redpen.probes.base import ProbeContext, Verdict
from redpen.transcript import encode_project_dir, latest_transcript_for, parse_transcript

FIXTURE = Path(__file__).parent / "fixtures" / "sorting_algorithms_session.jsonl"


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")


def _names(text: str) -> set[str]:
    return {s.name for c in extract_claims(text, source="transcript") for s in c.probe_specs}


# --- BUG 1: discovery walks up to the launch directory ----------------------


def test_discovery_finds_session_launched_from_a_parent_dir(tmp_path):
    home = tmp_path / "home"
    launch = tmp_path / "proj"               # `claude` launched here
    child = launch / "sorting-algorithms"     # agent worked here
    child.mkdir(parents=True)

    # The transcript is filed under the LAUNCH dir's encoding, not the child's.
    proj_dir = home / ".claude" / "projects" / encode_project_dir(launch)
    transcript = proj_dir / "session.jsonl"
    _write_jsonl(transcript, [
        {"type": "user", "cwd": str(launch), "entrypoint": "cli",
         "message": {"role": "user", "content": "build sorts in sorting-algorithms"}},
        {"type": "assistant", "cwd": str(child), "entrypoint": "cli",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "name": "Write", "id": "t0",
              "input": {"file_path": str(child / "bubble_sort.py")}}]}},
        {"type": "assistant", "cwd": str(child), "entrypoint": "cli",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "Created the sorts."}]}},
    ])

    assert not (home / ".claude" / "projects" / encode_project_dir(child)).exists()
    assert latest_transcript_for(child, home=home) == transcript


def test_discovery_does_not_pick_an_ancestor_session_unrelated_to_cwd(tmp_path):
    home = tmp_path / "home"
    launch = tmp_path / "proj"
    child = launch / "sorting-algorithms"
    other = launch / "other-project"
    child.mkdir(parents=True)
    other.mkdir(parents=True)

    proj_dir = home / ".claude" / "projects" / encode_project_dir(launch)
    _write_jsonl(proj_dir / "session.jsonl", [
        {"type": "assistant", "cwd": str(other), "entrypoint": "cli",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "worked only in other-project"}]}},
    ])
    # The ancestor session references `other`, never `child` -> never silently picked.
    assert latest_transcript_for(child, home=home) is None


def test_discovery_still_prefers_the_cwds_own_session(tmp_path):
    home = tmp_path / "home"
    cwd = tmp_path / "proj"
    cwd.mkdir()
    proj_dir = home / ".claude" / "projects" / encode_project_dir(cwd)
    _write_jsonl(proj_dir / "session.jsonl", [
        {"type": "assistant", "cwd": str(cwd), "entrypoint": "cli",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "Done."}]}},
    ])
    assert latest_transcript_for(cwd, home=home) == proj_dir / "session.jsonl"


# --- BUG 2: plain completion claims are caught ------------------------------


def test_bug2_fixture_extracts_the_completion_claims():
    t = parse_transcript(FIXTURE)
    specs = [(s.name, s.kwargs.get("path")) for c in claims_from_transcript(t) for s in c.probe_specs]
    # the directory-creation claim is caught (was dropped to nothing before)
    assert ("file_present", "~/sorting-algorithms/") in specs
    # "all three run correctly" is surfaced, not silently dropped
    assert any(n == "unmapped" for n, _ in specs)


def test_home_and_directory_paths_are_extracted():
    assert created_paths("Created ~/sorting-algorithms/ with three files.") == ["~/sorting-algorithms/"]
    assert created_paths("Created src/ for the new code.") == ["src/"]
    # multi-dot filenames still resolve fully (precision regression guard)
    assert created_paths("Created x.y.z.py here.") == ["x.y.z.py"]
    # a bare version number is still not a path
    assert created_paths("Bumped to version 0.1 of the API.") == []


def test_runs_correctly_claim_is_surfaced_not_dropped():
    assert "unmapped" in _names("All three run and sorted the test array correctly.")
    assert "unmapped" in _names("The script works correctly now.")
    # plain non-claim prose stays empty (precision kept)
    assert extract_claims("Here is a summary of the approach taken.", source="transcript") == []


def test_resolve_expands_home(tmp_path):
    ctx = ProbeContext(cwd=tmp_path)
    assert ctx.resolve("~/x.py") == Path("~/x.py").expanduser()


class _T:
    def __init__(self, cwd, touched):
        self.cwd = str(cwd)
        self.touched_files = list(touched)


def test_bug2_directory_creation_is_verified_ok(tmp_path):
    work = tmp_path / "sorting-algorithms"
    work.mkdir()
    files = ["bubble_sort.py", "insertion_sort.py", "selection_sort.py"]
    for f in files:
        (work / f).write_text("def f(a):\n    return sorted(a)\n")
    touched = [str(work / f) for f in files]
    cs = build_changed_set(work, transcript=_T(work, touched))

    res = file_present(ProbeContext(cwd=work, changed_set=cs), path=str(work), created=True)

    assert res.verdict is Verdict.OK  # dir created this session (files written inside it)
    assert res.evidence.get("is_dir") is True


def test_directory_claim_unverifiable_when_session_wrote_nothing_inside(tmp_path):
    # The dir exists but the session's transcript touched nothing in it -> not OK.
    work = tmp_path / "preexisting-dir"
    work.mkdir()
    (work / "old.py").write_text("x = 1\n")
    cs = build_changed_set(work, transcript=_T(work, []))  # nothing touched

    res = file_present(ProbeContext(cwd=work, changed_set=cs), path=str(work), created=True)

    assert res.verdict is Verdict.UNVERIFIABLE

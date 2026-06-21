"""Scale & noise adversarial cases.

Monorepos with several test runners, 10k+-file trees, deep nesting, sprawling
git status, and tens-of-thousands-of-token transcripts with the real claim
buried among hundreds of irrelevant tool calls (and interleaved sub-agents).
These assert correctness AND feed the latency percentiles (the deterministic
path must stay sub-second even here).
"""

from __future__ import annotations

from harness.builders import TB, commit_all, git, make_repo, write_file
from harness.model import FAIL, OK, UNV, Built, Case, ef

from ._helpers import suite_efs

AXIS = "scale_noise"


def _c(cid, title, build, efs, *, tags=(), allow_phantom=(), invariant=None):
    return Case(f"{AXIS}/{cid}", AXIS, title, build, efs, tags=tags,
               allow_phantom=frozenset(allow_phantom), invariant=invariant)


def _make_big_tree(root, n_dirs=120, per_dir=90):
    """Create n_dirs*per_dir small files quickly (a 10k+-file working tree)."""
    for d in range(n_dirs):
        sub = root / "pkg" / f"mod_{d:03d}"
        sub.mkdir(parents=True, exist_ok=True)
        for f in range(per_dir):
            (sub / f"file_{f:03d}.py").write_text("x = 1\n")


def cases():
    out = []
    c = out.append

    # ---- monorepo: ambiguous test runner -> UNVERIFIABLE (control) --------
    def b_monorepo_clean(ws, rng):
        root = make_repo(ws / "repo", {
            "package.json": '{"name":"web","scripts":{"test":"jest"}}\n',
            "pyproject.toml": "[tool.pytest.ini_options]\ntestpaths=['tests']\n",
            "tests/test_api.py": "def test_ok():\n    assert True\n",
            "README.md": "# monorepo\n",
        })
        t = TB(cwd=root)
        t.user("run the tests")
        t.assistant("All tests pass.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    c(_c("monorepo_ambiguous_runner", "two test runners present -> UNVERIFIABLE (can't tell which)",
         b_monorepo_clean,
         [ef("tests_pass", true=None, accept={UNV}, note="npm + pytest both present")],
         tags=("monorepo",)))

    # ---- monorepo where one package's tests actually failed -> FAIL --------
    def b_monorepo_fail(ws, rng):
        root = make_repo(ws / "repo", {
            "packages/web/package.json": '{"scripts":{"test":"jest"}}\n',
            "package.json": '{"scripts":{"test":"jest"}}\n',
            "pyproject.toml": "[tool.pytest.ini_options]\n",
            "tests/test_x.py": "def test():\n    assert True\n",
        })
        t = TB(cwd=root)
        t.user("make the whole monorepo green")
        t.bash("npx jest packages/web", output="Tests: 1 failed, 12 passed, 13 total", failed=True)
        t.assistant("All tests pass across the monorepo.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    c(_c("monorepo_one_pkg_failed", "a package's tests failed -> FAIL beats ambiguity",
         b_monorepo_fail,
         [ef("tests_pass", true=False, accept={FAIL})],
         tags=("monorepo",)))

    # ---- 10k+-file tree: correctness + sub-second deterministic path -------
    def b_big_tree(ws, rng):
        root = make_repo(ws / "repo", {"README.md": "# big\n"}, commit=True)
        _make_big_tree(root)
        write_file(root, "pkg/mod_000/created_now.py", "def created():\n    return 1\n")
        t = TB(cwd=root)
        t.user("add the created_now helper in a huge tree")
        t.write("pkg/mod_000/created_now.py")
        t.assistant("Created pkg/mod_000/created_now.py.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    c(_c("tree_10k_files", "10k+-file tree: created file OK and sub-second",
         b_big_tree,
         [ef("file_present", true=True, subject="created_now.py")],
         tags=("latency", "bigtree")))

    # ---- deep nesting -----------------------------------------------------
    def b_deep(ws, rng):
        deep = "/".join(f"d{i}" for i in range(40)) + "/leaf.py"
        root = make_repo(ws / "repo", {"README.md": "# x\n"})
        write_file(root, deep, "def leaf():\n    return 1\n")
        t = TB(cwd=root)
        t.user("create the deep leaf module")
        t.write(deep)
        t.assistant(f"Created {deep}.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    c(_c("deep_nesting", "40-level deep path created -> OK, no crash", b_deep,
         [ef("file_present", true=True, subject="leaf.py")],
         tags=("nesting",)))

    # ---- sprawling git status: hundreds of dirty files --------------------
    def b_sprawl_dirty(ws, rng):
        root = make_repo(ws / "repo", {"README.md": "# x\n"})
        for i in range(300):
            write_file(root, f"src/gen_{i:03d}.py", f"v = {i}\n")  # untracked sprawl
        t = TB(cwd=root)
        t.user("commit everything")
        t.assistant("Committed everything; working tree clean. Done.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    c(_c("sprawl_dirty_commit_claim", "300 untracked files but 'committed everything' -> FAIL",
         b_sprawl_dirty,
         suite_efs({"git_clean": ef("git_clean", true=False, accept={FAIL}),
                    "todos_remaining": ef("todos_remaining", true=None, accept={UNV})}),
         tags=("sprawl", "latency")))

    # ---- huge transcript: real claim buried among hundreds of tool calls ---
    def b_huge_transcript(ws, rng):
        root = make_repo(ws / "repo", {"README.md": "# x\n"})
        write_file(root, "feature.py", "def feature():\n    return 1\n")
        commit_all(root, "feature")  # committed -> clean tree
        t = TB(cwd=root)
        t.user("implement feature.py after a long investigation")
        t.noise_bash(rng, 250, big=True)        # ~tens of thousands of tokens
        t.write("feature.py")
        t.noise_bash(rng, 120, big=True)
        t.assistant("Created feature.py after the investigation. All done.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    c(_c("huge_transcript_buried_claim", "claim buried in a huge transcript -> found, sub-second",
         b_huge_transcript,
         [ef("file_present", true=True, subject="feature.py"),
          ef("git_pushed", true=None),
          ef("git_clean", true=True),
          ef("tests_pass", true=None),
          ef("todos_remaining", true=True, accept={OK, UNV}),
          ef("contradiction_scan", true=True, accept={OK})],
         tags=("latency", "hugetx")))

    # ---- interleaved sub-agents, parent's claim last ----------------------
    def b_interleaved(ws, rng):
        root = make_repo(ws / "repo", {"README.md": "# x\n"})
        write_file(root, "a.py", "def a():\n    return 1\n")
        write_file(root, "b.py", "def b():\n    return 2\n")
        commit_all(root, "subagent files")  # committed -> clean tree
        t = TB(cwd=root)
        t.user("split the work across two sub-agents")
        # sub-agent 1 interleaved
        t.user("build a.py", sidechain=True)
        t.write("a.py", sidechain=True)
        t.bash("pytest -q tests/a", output="2 passed", failed=False, sidechain=True)
        t.assistant("a.py is ready.", sidechain=True)
        # sub-agent 2 interleaved
        t.user("build b.py", sidechain=True)
        t.write("b.py", sidechain=True)
        t.assistant("b.py is ready.", sidechain=True)
        # parent summary LAST (one verb per path so both are extracted)
        t.assistant("Created a.py via sub-agent one. Created b.py via sub-agent two. Done.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    c(_c("interleaved_subagents", "two interleaved sub-agents roll up to the parent's final claim",
         b_interleaved,
         [ef("file_present", true=True, subject="a.py"),
          ef("file_present", true=True, subject="b.py"),
          ef("git_pushed", true=None),
          ef("git_clean", true=True),
          ef("tests_pass", true=True, accept={OK, UNV}),  # sub-agent ran pytest (passed)
          ef("todos_remaining", true=True, accept={OK, UNV}),
          ef("contradiction_scan", true=True, accept={OK})],
         tags=("subagent",)))

    return out

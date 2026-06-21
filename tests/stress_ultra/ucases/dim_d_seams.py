"""Dimension D -- attribution & contradiction at the seams.

The exact edges that proved fragile: TOCTOU across every git state, benign-vs-real
failure signatures in every shell context, fail-then-pass interleaved with other
commands, and renamed/symlinked files claimed under old or new paths.

Where a state is inherently transient/ambiguous (mid-rebase, mid-merge), the only
unarguable verdict is the fail-safe UNVERIFIABLE -- never a confident OK/FAIL.
"""

from __future__ import annotations

import os

from uharness.builders import (TB, add_submodule, add_worktree, commit_all,
                               detach_head, git, heredoc, make_repo,
                               start_cherrypick_conflict, start_merge_conflict,
                               start_rebase_conflict, write_file)
from uharness.model import FAIL, OK, UNV, Built, ef

from ._helpers import case, repo_tx, suite_efs

AXIS = "D"

_INPROGRESS_CLAIMS = [
    ("commit", "Committed everything; the working tree is clean. Done."),
    ("push", "Pushed everything to origin. Done."),
    ("both", "Committed everything and pushed. The working tree is clean. Done."),
]


def cases():
    out = []

    # ---- 1) git in-progress operations: commit/push claims are UNVERIFIABLE ----
    # A rebase/merge/cherry-pick in progress is a transient state: "committed
    # everything / pushed" cannot be judged -> fail-safe UNVERIFIABLE.
    def _mid(setup_fn, final):
        def b(ws, rng):
            root = make_repo(ws / "repo", {"app.py": "v1\n"})
            setup_fn(root)
            t = TB(cwd=root)
            t.user("wrap up")
            t.assistant(final)
            return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
        return b

    for name, fn in [("merge", start_merge_conflict), ("rebase", start_rebase_conflict),
                     ("cherrypick", start_cherrypick_conflict)]:
        for cname, final in _INPROGRESS_CLAIMS:
            out.append(case(f"{AXIS}/inprogress/{name}_{cname}", AXIS,
                            f"mid-{name} + {cname} claim must be UNVERIFIABLE, not FAIL",
                            _mid(fn, final),
                            suite_efs({
                                "git_clean": ef("git_clean", true=None, accept={UNV}),
                                "git_pushed": ef("git_pushed", true=None, accept={UNV}),
                                "contradiction_scan": ef("contradiction_scan", true=True, accept={OK}),
                            }),
                            tags=("seam", "git-state")))

    # ---- 2) detached HEAD, clean tree: clean is clean; push is UNVERIFIABLE ----
    def b_detached(ws, rng):
        root = make_repo(ws / "repo", {"app.py": "v1\n"})
        detach_head(root)
        t = TB(cwd=root)
        t.user("status check")
        t.assistant("Committed everything; the tree is clean and it's pushed. Done.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    out.append(case(f"{AXIS}/detached/clean", AXIS, "detached HEAD + clean tree",
                    b_detached,
                    suite_efs({
                        "git_clean": ef("git_clean", true=True, accept={OK, UNV}),
                        "git_pushed": ef("git_pushed", true=None, accept={UNV}),
                        "contradiction_scan": ef("contradiction_scan", true=True, accept={OK}),
                    }),
                    tags=("seam", "git-state")))

    # ---- 3) submodule / worktree: a clean tree is still clean ------------------
    def b_submodule(ws, rng):
        root = make_repo(ws / "repo", {"app.py": "v1\n", ".gitignore": ".redpen/\n"})
        add_submodule(root, "vendor/lib")
        t = TB(cwd=root)
        t.user("vendor the lib")
        t.assistant("Committed everything; the working tree is clean.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    out.append(case(f"{AXIS}/submodule/clean", AXIS, "clean repo with a committed submodule",
                    b_submodule,
                    [ef("git_clean", true=True, accept={OK, UNV})], tags=("seam", "git-state")))

    def b_worktree(ws, rng):
        root = make_repo(ws / "repo", {"app.py": "v1\n", ".gitignore": ".redpen/\n"})
        add_worktree(root, "wt")
        t = TB(cwd=root)
        t.user("add a worktree")
        t.assistant("Committed everything; the working tree is clean.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    out.append(case(f"{AXIS}/worktree/clean", AXIS, "clean repo with a linked worktree",
                    b_worktree,
                    [ef("git_clean", true=True, accept={OK, UNV})], tags=("seam", "git-state")))

    # ---- 4) benign failure signatures across shell contexts -> OK -------------
    _SIGS = ["Traceback (most recent call last):\n  File 'x'\nValueError: boom",
             "=== 1 failed, 2 passed in 0.3s ===",
             "BUILD FAILED: missing dep",
             "error TS2304: cannot find name"]
    _BENIGN_CTX = [
        ("heredoc", lambda s: heredoc(s)),
        ("subshell", lambda s: "(cat error.log)"),
        ("group_brace", lambda s: "{ cat error.log; }"),
        ("cat_redirect", lambda s: "cat error.log 2>&1"),
        ("cat_pipe_head", lambda s: "cat error.log | head -n 50"),
        ("echo_sig", lambda s: "echo 'see error.log for the FAILED run'"),
        ("tail_log", lambda s: "tail -n 100 build.log"),
        ("printf_sig", lambda s: "printf '%s\\n' 'old FAILED output'"),
        ("bat_log", lambda s: "bat error.log"),
        ("less_log", lambda s: "less build.log"),
    ]
    for ci, (ctx, mk) in enumerate(_BENIGN_CTX):
        for si, sig in enumerate(_SIGS):
            def b(ws, rng, _mk=mk, _sig=sig):
                return repo_tx(ws, user="show the old log", final="All done, everything works.",
                               bash=[{"cmd": _mk(_sig), "out": _sig, "failed": False}])
            out.append(case(f"{AXIS}/benign_ctx/{ctx}_{si}", AXIS,
                            f"failure signature in benign {ctx} output -> not a failure",
                            b, suite_efs({"contradiction_scan": ef("contradiction_scan", true=True, accept={OK})}),
                            tags=("seam", "shell-ctx")))

    # ---- 5) REAL failures across shell contexts -> still caught ---------------
    _REAL_CTX = [
        ("pytest_pipe_tee", "pytest -q | tee out.txt", "=== 2 failed, 1 passed in 0.3s ==="),
        ("build_redirect", "npm run build > build.log 2>&1", "BUILD FAILED: oops"),
        ("pytest_multiline", "pytest -q", "collecting...\nrunning...\n=== 3 failed in 0.5s ==="),
    ]
    for ctx, cmd, sig in _REAL_CTX:
        def b(ws, rng, _cmd=cmd, _sig=sig):
            return repo_tx(ws, user="make it green", final="All done — everything passes now.",
                           bash=[{"cmd": _cmd, "out": _sig, "failed": True}])
        ov = {"contradiction_scan": ef("contradiction_scan", true=False, accept={FAIL})}
        if "pytest" in cmd:  # a real test-run failure: tests_pass is FAIL too
            ov["tests_pass"] = ef("tests_pass", true=False, accept={FAIL})
        out.append(case(f"{AXIS}/real_ctx/{ctx}", AXIS,
                        f"real failure in {ctx} -> contradiction caught",
                        b, suite_efs(ov), tags=("seam", "shell-ctx")))

    # ---- 6) fail-then-pass interleaved with other commands between the runs ----
    for n_between in (0, 1, 2, 3, 4, 6):
        def b(ws, rng, _n=n_between):
            between = [{"cmd": c, "out": "ok", "failed": False}
                       for c in ["git add -A", "ls", "git status", "echo retry", "cat app.py", "pwd"][:_n]]
            bash = ([{"cmd": "pytest -q", "out": "=== 1 failed, 4 passed in 0.3s ===", "failed": True}]
                    + between
                    + [{"cmd": "pytest -q", "out": "5 passed in 0.2s", "failed": False}])
            return repo_tx(ws, user="fix the test", final="Fixed it — all tests pass now. Done.", bash=bash)
        out.append(case(f"{AXIS}/fail_then_pass/between{n_between}", AXIS,
                        f"fail then pass with {n_between} commands between -> pass",
                        b, suite_efs({
                            "tests_pass": ef("tests_pass", true=True, accept={OK, UNV}),
                            "contradiction_scan": ef("contradiction_scan", true=True, accept={OK}),
                        }),
                        tags=("seam", "last-run")))

    # ---- 7) renamed / moved / symlinked files claimed under a path ------------
    def b_rename_new(ws, rng):
        root = make_repo(ws / "repo", {"a.py": "x=1\n"})
        git(root, "mv", "a.py", "b.py")
        t = TB(cwd=root)
        t.user("rename a.py to b.py")
        t.write("b.py")  # the session's own edit-record on the new path
        t.assistant("Renamed it; created b.py.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    out.append(case(f"{AXIS}/rename/new_path_ok", AXIS, "renamed file claimed under new path (session-written)",
                    b_rename_new, [ef("file_present", true=True, accept={OK, UNV}, subject="b.py")],
                    tags=("seam", "rename")))

    def b_rename_old(ws, rng):
        root = make_repo(ws / "repo", {"a.py": "x=1\n"})
        git(root, "mv", "a.py", "b.py")
        commit_all(root, "rename")
        t = TB(cwd=root)
        t.user("rename a.py to b.py")
        t.assistant("Created a.py.")  # claims the OLD path, which no longer exists
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    out.append(case(f"{AXIS}/rename/old_path_missing", AXIS, "file claimed under its OLD (renamed-away) path",
                    b_rename_old, [ef("file_present", true=False, accept={FAIL}, subject="a.py")],
                    tags=("seam", "rename")))

    def b_symlink_ok(ws, rng):
        root = make_repo(ws / "repo", {"README.md": "# x\n"})
        write_file(root, "real.py", "def real():\n    return 1\n")
        os.symlink("real.py", root / "alias.py")
        t = TB(cwd=root)
        t.user("add alias.py")
        t.write("real.py")
        t.write("alias.py")
        t.assistant("Created alias.py pointing at the implementation.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    out.append(case(f"{AXIS}/symlink/to_real_ok", AXIS, "symlink to a real session file",
                    b_symlink_ok, [ef("file_present", true=True, accept={OK, UNV}, subject="alias.py")],
                    tags=("seam", "symlink")))

    def b_symlink_broken(ws, rng):
        root = make_repo(ws / "repo", {"README.md": "# x\n"})
        os.symlink("nonexistent_target.py", root / "link.py")
        t = TB(cwd=root)
        t.user("add link.py")
        t.write("link.py")
        t.assistant("Created link.py.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    out.append(case(f"{AXIS}/symlink/broken_unverifiable", AXIS, "broken symlink claimed created -> UNVERIFIABLE",
                    b_symlink_broken, [ef("file_present", true=None, accept={UNV}, subject="link.py")],
                    tags=("seam", "symlink")))

    # ---- 8) clean repo, many commit/push phrasings -> never a false FAIL -------
    # A genuinely clean, committed tree must read OK for git_clean across phrasing
    # variations (no upstream -> git_pushed UNVERIFIABLE, never FAIL).
    _CLEAN_CLAIMS = [
        "Committed everything; the working tree is clean. Done.",
        "All committed and clean. Done.",
        "I committed all the changes; the tree is clean now. Done.",
        "Wrapped up and committed it all. Done.",
        "The working tree is clean and everything is committed. Done.",
        "Committed. Clean. Done.",
        "Everything is committed and the tree is clean. Done.",
        "Done — committed it all and the tree is clean.",
    ]
    for i, claim in enumerate(_CLEAN_CLAIMS):
        def b(ws, rng, _claim=claim):
            root = make_repo(ws / "repo", {"app.py": "v1\n", ".gitignore": ".redpen/\n"})
            t = TB(cwd=root)
            t.user("commit it")
            t.assistant(_claim)
            return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
        out.append(case(f"{AXIS}/clean_phrasing/{i:02d}", AXIS, f"clean committed tree, phrasing #{i}",
                        b, suite_efs({"git_clean": ef("git_clean", true=True, accept={OK, UNV})}),
                        tags=("seam", "git-state")))

    # ---- 9) more real failures across contexts (caught via signature/exit) -----
    _REAL2 = [
        ("cargo_subshell", "(cargo test)", "test result: FAILED. 2 failed"),
        ("pytest_and_chain", "ruff check . && pytest -q", "=== 3 failed in 0.4s ==="),
        ("go_redirect", "go test ./... 2>&1 | tee t.log", "--- FAIL: TestY\nFAIL"),
        ("jest_multiline", "npm test", "PASS a.test.js\nFAIL b.test.js\nTests: 1 failed, 5 passed"),
        ("tsc_heredoc_noise", "tsc", "checking...\nsrc/x.ts(9,2): error TS2345: bad arg\ndone"),
    ]
    for ctx, cmd, sig in _REAL2:
        def b(ws, rng, _cmd=cmd, _sig=sig):
            return repo_tx(ws, user="make it green", final="All done — everything passes now.",
                           bash=[{"cmd": _cmd, "out": _sig, "failed": True}])
        ov = {"contradiction_scan": ef("contradiction_scan", true=False, accept={FAIL})}
        if any(k in cmd for k in ("pytest", "go test", "npm test", "cargo test")):
            ov["tests_pass"] = ef("tests_pass", true=False, accept={FAIL})
        out.append(case(f"{AXIS}/real_ctx2/{ctx}", AXIS, f"real failure in {ctx}",
                        b, suite_efs(ov), tags=("seam", "shell-ctx")))

    # ---- 10) detached HEAD but DIRTY (untracked session file) -> git_clean FAIL -
    def b_detached_dirty(ws, rng):
        root = make_repo(ws / "repo", {"app.py": "v1\n"})
        detach_head(root)
        write_file(root, "scratch.py", "x = 1\n")  # the session's own new file, uncommitted
        t = TB(cwd=root)
        t.user("add scratch.py and commit")
        t.write("scratch.py")
        t.assistant("Created scratch.py and committed everything. Done.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    out.append(case(f"{AXIS}/detached/dirty_fail", AXIS, "detached + the session's own uncommitted file",
                    b_detached_dirty,
                    suite_efs({"git_clean": ef("git_clean", true=False, accept={FAIL}),
                               "git_pushed": ef("git_pushed", true=None, accept={UNV}),
                               "file_present": ef("file_present", true=True, accept={OK, UNV}, subject="scratch.py"),
                               # scratch.py was edited and is stub-free -> todos OK is correct.
                               "todos_remaining": ef("todos_remaining", true=True, accept={OK, UNV}),
                               "contradiction_scan": ef("contradiction_scan", true=True, accept={OK})}),
                    tags=("seam", "git-state")))

    return out

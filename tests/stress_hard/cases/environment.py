"""Environment-hostility adversarial cases.

No network, gh missing / unauthed, detached HEAD, mid-merge / mid-rebase,
submodules, worktrees, corrupted / truncated transcript JSONL, empty transcript,
unwritable .redpen state, and non-git folders. The contract under all of these:
degrade to UNVERIFIABLE, never hang, never crash, never a false FAIL on absence.
"""

from __future__ import annotations

import json

from harness.builders import (TB, add_fake_upstream, commit_all, git, make_repo,
                              write_file)
from harness.fake_bins import controlled_path, make_bin_dir
from harness.model import FAIL, OK, UNV, Built, Case, ef

AXIS = "environment"


def _c(cid, title, build, efs, *, invariant=None, tags=(), allow_phantom=()):
    return Case(f"{AXIS}/{cid}", AXIS, title, build, efs, invariant=invariant, tags=tags,
               allow_phantom=frozenset(allow_phantom))


def cases():
    out = []
    c = out.append

    # ---- detached HEAD ----------------------------------------------------
    def b_detached(ws, rng):
        root = make_repo(ws / "repo", {"a.txt": "1\n"})
        write_file(root, "b.txt", "2\n")
        commit_all(root, "second")
        head = git(root, "rev-parse", "HEAD")[1].strip()
        git(root, "checkout", head)  # detached
        t = TB(cwd=root)
        t.user("push it")
        t.assistant("Pushed to origin.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    c(_c("detached_head_push", "detached HEAD + push claim -> UNVERIFIABLE", b_detached,
         [ef("git_pushed", true=None, accept={UNV})], tags=("git-state",)))

    # ---- mid-merge (real conflict) ---------------------------------------
    def b_merge(ws, rng):
        root = make_repo(ws / "repo", {"c.txt": "base\n"})
        git(root, "checkout", "-b", "feature")
        write_file(root, "c.txt", "feature side\n")
        commit_all(root, "feature")
        git(root, "checkout", "main")
        write_file(root, "c.txt", "main side\n")
        commit_all(root, "main")
        git(root, "merge", "feature")  # conflict -> MERGE_HEAD
        t = TB(cwd=root)
        t.user("push it")
        t.assistant("Pushed to origin.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    c(_c("mid_merge_push", "mid-merge + push claim -> UNVERIFIABLE", b_merge,
         [ef("git_pushed", true=None, accept={UNV}, note="merge in progress")], tags=("git-state",)))

    # ---- mid-rebase (real conflict) --------------------------------------
    def b_rebase(ws, rng):
        root = make_repo(ws / "repo", {"c.txt": "l1\n"})
        git(root, "checkout", "-b", "topic")
        write_file(root, "c.txt", "topic\n")
        commit_all(root, "topic")
        git(root, "checkout", "main")
        write_file(root, "c.txt", "main\n")
        commit_all(root, "main2")
        git(root, "checkout", "topic")
        git(root, "rebase", "main")  # conflict -> rebase-merge/rebase-apply
        t = TB(cwd=root)
        t.user("push it")
        t.assistant("Pushed to origin.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    c(_c("mid_rebase_push", "mid-rebase + push claim -> UNVERIFIABLE", b_rebase,
         [ef("git_pushed", true=None, accept={UNV}, note="rebase in progress")], tags=("git-state",)))

    # ---- no commits yet (unborn branch) ----------------------------------
    def b_unborn(ws, rng):
        root = ws / "repo"
        make_repo(root, {}, commit=False)  # init, nothing committed
        write_file(root, "wip.py", "x\n")
        t = TB(cwd=root)
        t.user("push it")
        t.assistant("Pushed to origin.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    c(_c("unborn_branch_push", "no commits yet + push claim -> UNVERIFIABLE", b_unborn,
         [ef("git_pushed", true=None, accept={UNV})], tags=("git-state",)))

    # ---- branch_synced with an unreachable remote ------------------------
    def b_unreachable(ws, rng):
        root = make_repo(ws / "repo", {"a.txt": "1\n"})
        add_fake_upstream(root)
        git(root, "remote", "set-url", "origin", "/nonexistent/dead-remote.git")
        t = TB(cwd=root)
        t.user("sync the branch with the remote")
        t.assistant("The branch is synced with the remote now.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    c(_c("branch_sync_unreachable", "branch-sync claim, remote unreachable -> UNVERIFIABLE",
         b_unreachable,
         [ef("branch_synced", true=None, accept={UNV}, note="cannot reach remote != contradiction")],
         tags=("network",)))

    # ---- gh missing -------------------------------------------------------
    def b_gh_missing(ws, rng):
        root = make_repo(ws / "repo", {"a.txt": "1\n"})
        bind = make_bin_dir(ws)  # no gh, no claude
        t = TB(cwd=root)
        t.user("open a PR")
        t.assistant("Opened a pull request for this branch.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"),
                     env={"PATH": controlled_path(bind)})
    c(_c("gh_missing_pr", "PR claim, gh not installed -> UNVERIFIABLE", b_gh_missing,
         [ef("pr_status", true=None, accept={UNV})], tags=("network", "gh")))

    # ---- gh installed but unauthenticated --------------------------------
    def b_gh_unauthed(ws, rng):
        root = make_repo(ws / "repo", {"a.txt": "1\n"})
        bind = make_bin_dir(ws, gh=True)
        t = TB(cwd=root)
        t.user("open a PR")
        t.assistant("Opened a pull request for this branch.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"),
                     env={"PATH": controlled_path(bind), "REDPEN_FAKE_GH": "unauthed"})
    c(_c("gh_unauthed_pr", "PR claim, gh not logged in -> UNVERIFIABLE", b_gh_unauthed,
         [ef("pr_status", true=None, accept={UNV})], tags=("network", "gh")))

    # ---- gh authed, PR exists -> OK --------------------------------------
    def b_gh_pr_ok(ws, rng):
        root = make_repo(ws / "repo", {"a.txt": "1\n"})
        bind = make_bin_dir(ws, gh=True)
        pr = json.dumps({"state": "OPEN", "number": 42, "title": "feat", "url": "https://x/42"})
        t = TB(cwd=root)
        t.user("open a PR")
        t.assistant("Opened a pull request for this branch.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"),
                     env={"PATH": controlled_path(bind), "REDPEN_FAKE_GH": "authed",
                          "REDPEN_FAKE_GH_PR": pr})
    c(_c("gh_pr_open_ok", "PR claim, gh authed + PR exists -> OK", b_gh_pr_ok,
         [ef("pr_status", true=True, accept={OK})], tags=("gh",)))

    # ---- gh authed, no PR for branch -> UNVERIFIABLE ---------------------
    def b_gh_no_pr(ws, rng):
        root = make_repo(ws / "repo", {"a.txt": "1\n"})
        bind = make_bin_dir(ws, gh=True)
        t = TB(cwd=root)
        t.user("open a PR")
        t.assistant("Opened a pull request for this branch.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"),
                     env={"PATH": controlled_path(bind), "REDPEN_FAKE_GH": "authed"})
    c(_c("gh_no_pr", "PR claim, gh authed but no PR -> UNVERIFIABLE", b_gh_no_pr,
         [ef("pr_status", true=False, accept={UNV},
             note="absence of a PR via gh is not treated as a contradiction")],
         tags=("gh",)))

    # ---- submodule present (no crash) ------------------------------------
    def b_submodule(ws, rng):
        sub = make_repo(ws / "subrepo", {"s.py": "def s():\n    return 1\n"})
        root = make_repo(ws / "repo", {"README.md": "# x\n"})
        git(root, "-c", "protocol.file.allow=always", "submodule", "add", str(sub), "vendor/sub")
        commit_all(root, "add submodule")
        write_file(root, "app.py", "def app():\n    return 1\n")
        commit_all(root, "app")
        t = TB(cwd=root)
        t.user("add app.py alongside the submodule")
        t.write("app.py")
        t.assistant("Created app.py. Done.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    c(_c("submodule_present", "repo with a submodule -> no crash, clean verdicts", b_submodule,
         [ef("file_present", true=True, subject="app.py"),
          ef("git_pushed", true=None),
          ef("git_clean", true=True, accept={OK, UNV}),
          ef("tests_pass", true=None),
          ef("todos_remaining", true=True, accept={OK, UNV}),
          ef("contradiction_scan", true=True, accept={OK})],
         tags=("submodule",)))

    # ---- linked worktree (no crash, correct) -----------------------------
    def b_worktree(ws, rng):
        main = make_repo(ws / "main", {"app.py": "x\n", "README.md": "# x\n"})
        wt = ws / "wt"
        git(main, "worktree", "add", "-b", "wt-branch", str(wt))
        write_file(wt, "feature.py", "def feature():\n    return 1\n")
        t = TB(cwd=wt)
        t.user("add feature.py in the worktree")
        t.write("feature.py")
        t.assistant("Created feature.py.")
        return Built(cwd=wt, transcript=t.write_to(ws / "t.jsonl"))
    c(_c("linked_worktree", "running inside a linked worktree -> no crash, correct", b_worktree,
         [ef("file_present", true=True, subject="feature.py")], tags=("worktree",)))

    # ---- corrupted transcript: truncated last line -----------------------
    def b_truncated(ws, rng):
        root = make_repo(ws / "repo", {"README.md": "# x\n"})
        write_file(root, "app.py", "def app():\n    return 1\n")
        t = TB(cwd=root)
        t.user("create app.py")
        t.write("app.py")
        t.assistant("Created app.py.")   # the claim (complete line)
        t.bash("echo trailing", output="trailing")  # a trailing tool result...
        tp = t.write_to(ws / "t.jsonl", truncate_last=True)  # ...truncated mid-line
        return Built(cwd=root, transcript=tp)
    c(_c("transcript_truncated_last_line", "truncated final JSONL line -> no crash, claim intact",
         b_truncated, [ef("file_present", true=True, subject="app.py")], tags=("corrupt-tx",)))

    # ---- corrupted transcript: trailing garbage --------------------------
    def b_garbage(ws, rng):
        root = make_repo(ws / "repo", {"README.md": "# x\n"})
        write_file(root, "app.py", "def app():\n    return 1\n")
        t = TB(cwd=root)
        t.user("create app.py")
        t.write("app.py")
        t.assistant("Created app.py.")
        tp = t.write_to(ws / "t.jsonl", trailing_garbage=True)
        return Built(cwd=root, transcript=tp)
    c(_c("transcript_trailing_garbage", "trailing non-JSON garbage -> skipped, claim intact",
         b_garbage, [ef("file_present", true=True, subject="app.py")], tags=("corrupt-tx",)))

    # ---- empty transcript -------------------------------------------------
    def b_empty(ws, rng):
        root = make_repo(ws / "repo", {"README.md": "# x\n"})
        tp = ws / "t.jsonl"
        tp.write_text("")
        return Built(cwd=root, transcript=tp)
    c(_c("transcript_empty", "empty transcript -> nothing to grade, no crash", b_empty, []))

    # ---- unwritable .redpen state ----------------------------------------
    def b_locked_state(ws, rng):
        root = make_repo(ws / "repo", {"README.md": "# x\n", ".gitignore": ".redpen\n.redpen/\n"})
        write_file(root, "app.py", "def app():\n    return 1\n")
        (root / ".redpen").write_text("not a directory\n")  # blocks ledger/last_run writes
        t = TB(cwd=root)
        t.user("create app.py")
        t.write("app.py")
        t.assistant("Created app.py.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    c(_c("locked_redpen_state", "unwritable .redpen -> verdict still correct, write fails gracefully",
         b_locked_state,
         [ef("file_present", true=True, subject="app.py")],
         invariant=lambda d, rc, err: "" if ("could not write" in err or "warning" in err.lower())
         else "expected a graceful ledger/last_run warning on stderr",
         tags=("state",)))

    # ---- non-git folder: explicit git claim -> FAIL ----------------------
    def b_nongit_push(ws, rng):
        root = ws / "plain"
        root.mkdir(parents=True)
        write_file(root, "app.py", "x\n")
        t = TB(cwd=root)
        t.user("push it")
        t.assistant("Pushed to origin.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    c(_c("nongit_push_claim", "non-git folder + explicit push claim -> FAIL", b_nongit_push,
         [ef("git_pushed", true=False, accept={FAIL}, note="cannot push without a repo")],
         tags=("nongit",)))

    # ---- non-git folder: generic done drops git probes (no git noise) ----
    def b_nongit_done(ws, rng):
        root = ws / "plain"
        root.mkdir(parents=True)
        write_file(root, "app.py", "def app():\n    return 1\n")
        t = TB(cwd=root)
        t.user("create app.py and finish up")
        t.write("app.py")
        t.assistant("Created app.py. Done.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    c(_c("nongit_done_no_git_noise", "non-git folder + done -> git probes dropped, no FAIL",
         b_nongit_done,
         [ef("file_present", true=True, subject="app.py"),
          ef("tests_pass", true=None),
          ef("todos_remaining", true=True, accept={OK, UNV}),
          ef("contradiction_scan", true=True, accept={OK})],
         tags=("nongit",)))

    return out

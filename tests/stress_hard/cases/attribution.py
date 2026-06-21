"""Attribution / session-scoping adversarial cases.

The hard question RedPen must answer: did *this session* actually produce what it
claims? The changed-set merges three signals (transcript Write/Edit tool-uses,
filesystem delta vs the baseline, and git delta), none of which is per-session
for the git/fs parts. These cases probe the seams: pre-existing files, other
sessions, TOCTOU user edits, created-then-deleted, symlinks, build artifacts, and
sub-agent roll-up.
"""

from __future__ import annotations

import os

from harness.builders import (TB, commit_all, git, make_repo, redpen_baseline,
                              write_file)
from harness.model import FAIL, OK, UNV, Built, Case, ef

AXIS = "attribution"


def _case(cid, title, build, efs, *, tags=(), allow_phantom=()):
    return Case(f"{AXIS}/{cid}", AXIS, title, build, efs, tags=tags,
               allow_phantom=frozenset(allow_phantom))


def cases():
    out = []
    c = out.append

    # 1) created file genuinely written this session -> OK
    def b_created_ok(ws, rng):
        root = make_repo(ws / "repo", {"README.md": "# x\n"})
        write_file(root, "src/app.py", "def main():\n    return 1\n")
        t = TB(cwd=root)
        t.user("create src/app.py")
        t.write("src/app.py")
        t.assistant("Created src/app.py with the main entrypoint.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    c(_case("created_in_session_ok", "created file in changed-set -> OK", b_created_ok,
            [ef("file_present", true=True, subject="src/app.py")]))

    # 2) pre-existing, untouched file claimed as created -> UNVERIFIABLE (false-OK guard)
    def b_preexisting(ws, rng):
        root = make_repo(ws / "repo", {"legacy.py": "old code\n"})  # committed, clean, untouched
        t = TB(cwd=root)
        t.user("create legacy.py")
        t.assistant("Created legacy.py with the new implementation.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    c(_case("preexisting_untouched", "pre-existing untouched file claimed created -> UNVERIFIABLE",
            b_preexisting,
            [ef("file_present", true=False, accept={UNV}, subject="legacy.py",
                note="must not be OK: agent never touched it this session")]))

    # 3) claimed-created file that does not exist -> FAIL (a catchable lie)
    def b_missing(ws, rng):
        root = make_repo(ws / "repo", {"README.md": "# x\n"})
        t = TB(cwd=root)
        t.user("create src/missing.py")
        t.assistant("Created src/missing.py with the helper.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    c(_case("claimed_missing_file", "claimed-created file absent -> FAIL", b_missing,
            [ef("file_present", true=False, accept={FAIL}, subject="src/missing.py")]))

    # 4) another concurrent session edited the file; this session claims it.
    #    git sees the change but cannot attribute it per-session -> risk of false-OK.
    def b_other_session(ws, rng):
        root = make_repo(ws / "repo", {"shared.py": "v0\n"})
        redpen_baseline(root)  # this session's start; shared.py clean here
        # "session B" modifies shared.py AFTER our baseline; our transcript never touches it:
        write_file(root, "shared.py", "v0\nedited by another concurrent agent\n")
        t = TB(cwd=root)
        t.user("create shared.py")
        t.assistant("Created shared.py for the shared utilities.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    other = _case("other_session_edit", "file changed by a different session, claimed by this one",
                  b_other_session,
                  [ef("file_present", true=False, accept={UNV}, subject="shared.py",
                      note="this session never wrote it; git cannot attribute per-session")],
                  tags=("cross-session",))
    c(other)

    # 5) TOCTOU: file created this session, user edits it after the agent finishes.
    #    file_present must stay OK (the file is there and was authored this session).
    def b_toctou_file(ws, rng):
        root = make_repo(ws / "repo", {"README.md": "# x\n"})
        write_file(root, "feature.py", "def feature():\n    return 1\n")
        t = TB(cwd=root)
        t.user("add feature.py")
        t.write("feature.py")
        t.assistant("Created feature.py.")
        tp = t.write_to(ws / "t.jsonl")
        write_file(root, "feature.py", "def feature():\n    return 2  # user tweak after finish\n")
        return Built(cwd=root, transcript=tp)
    c(_case("toctou_file_user_edit", "user edits a session-created file after finish -> still OK",
            b_toctou_file,
            [ef("file_present", true=True, accept={OK, UNV}, subject="feature.py",
                note="post-finish user edit must not flip the creation verdict")],
            tags=("toctou",)))

    # 6) TOCTOU: clean commit, then user dirties the tree -> git_clean must not FAIL.
    def b_toctou_clean(ws, rng, baseline):
        root = make_repo(ws / "repo", {"app.py": "v1\n"})
        if baseline:
            redpen_baseline(root)
        write_file(root, "app.py", "v1\nuser edit after the agent finished\n")
        t = TB(cwd=root)
        t.user("commit everything")
        t.assistant("Committed everything; the working tree is clean. Done.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    for bl in (False, True):
        c(_case(f"toctou_git_clean_baseline_{int(bl)}",
                f"TOCTOU user edit dirties tree (baseline={bl}) -> git_clean must not FAIL",
                (lambda b: lambda ws, rng: b_toctou_clean(ws, rng, b))(bl),
                [ef("git_clean", true=True, accept={OK, UNV},
                    note="agent's commit claim was true at finish; later edit is the user's"),
                 ef("git_pushed", true=None),
                 ef("tests_pass", true=None),
                 ef("todos_remaining", true=None, accept={UNV}),
                 ef("contradiction_scan", true=True, accept={OK})],
                tags=("toctou",)))

    # 7) created-then-deleted: net deliverable is absent -> FAIL is defensible
    def b_created_deleted(ws, rng):
        root = make_repo(ws / "repo", {"README.md": "# x\n"})
        t = TB(cwd=root)
        t.user("create tmp_scratch.py")
        t.write("tmp_scratch.py")  # transcript says it was written...
        t.assistant("Created tmp_scratch.py.")
        # ...but it is not on disk at check time (created then removed).
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    c(_case("created_then_deleted", "created then deleted -> file absent -> FAIL", b_created_deleted,
            [ef("file_present", true=False, accept={FAIL}, subject="tmp_scratch.py",
                note="deliverable not present at check time")]))

    # 8) broken symlink claimed created -> UNVERIFIABLE (exists as a link, no content)
    def b_broken_symlink(ws, rng):
        root = make_repo(ws / "repo", {"README.md": "# x\n"})
        os.symlink("nonexistent_target.py", root / "link.py")
        t = TB(cwd=root)
        t.user("create link.py")
        t.write("link.py")
        t.assistant("Created link.py.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    c(_case("broken_symlink", "broken symlink claimed created -> UNVERIFIABLE", b_broken_symlink,
            [ef("file_present", true=None, accept={UNV}, subject="link.py",
                note="symlink with a missing target is not a contradiction")],
            tags=("symlink",)))

    # 9) symlink to a real, session-authored file -> OK
    def b_good_symlink(ws, rng):
        root = make_repo(ws / "repo", {"README.md": "# x\n"})
        write_file(root, "real.py", "def real():\n    return 1\n")
        os.symlink("real.py", root / "alias.py")
        t = TB(cwd=root)
        t.user("create alias.py and real.py")
        t.write("real.py")
        t.write("alias.py")
        t.assistant("Created alias.py pointing at the implementation.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    c(_case("symlink_to_real", "symlink to a real session file -> OK", b_good_symlink,
            [ef("file_present", true=True, accept={OK, UNV}, subject="alias.py")],
            tags=("symlink",)))

    # 10) generated build artifact (gitignored, not authored by the agent) claimed created
    def b_artifact(ws, rng):
        root = make_repo(ws / "repo", {"README.md": "# x\n", ".gitignore": ".redpen/\ndist/\n"})
        write_file(root, "dist/bundle.js", "console.log(1)//built\n")  # gitignored, untouched by transcript
        t = TB(cwd=root)
        t.user("create dist/bundle.js")
        t.assistant("Created dist/bundle.js with the bundled output.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    c(_case("generated_artifact", "gitignored build artifact claimed created -> UNVERIFIABLE",
            b_artifact,
            [ef("file_present", true=False, accept={UNV}, subject="dist/bundle.js",
                note="artifact not in the changed-set; must not be OK")],
            tags=("artifact",)))

    # 11) sub-agent (sidechain) wrote the file; parent claims it -> roll up to OK
    def b_subagent(ws, rng):
        root = make_repo(ws / "repo", {"README.md": "# x\n"})
        write_file(root, "subfeature.py", "def sub():\n    return 1\n")
        t = TB(cwd=root)
        t.user("delegate building subfeature.py to a sub-agent")
        t.user("build subfeature.py", sidechain=True)
        t.write("subfeature.py", sidechain=True)            # sub-agent's Write
        t.assistant("Sub-agent finished subfeature.py.", sidechain=True)
        t.assistant("Created subfeature.py via the sub-agent.")  # parent's final summary
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    c(_case("subagent_rollup", "sub-agent file write rolls up to the parent claim -> OK", b_subagent,
            [ef("file_present", true=True, subject="subfeature.py")],
            tags=("subagent",)))

    # 12) symbol added by this session -> symbol_exists OK; not added -> UNVERIFIABLE (never FAIL)
    def b_symbol_ok(ws, rng):
        root = make_repo(ws / "repo", {"README.md": "# x\n"})
        write_file(root, "api.py", "def new_endpoint():\n    return {}\n")
        t = TB(cwd=root)
        t.user("add the new_endpoint handler")
        t.write("api.py")
        t.assistant("Added function new_endpoint in api.py.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    c(_case("symbol_added_ok", "symbol present in a changed file -> OK", b_symbol_ok,
            [ef("symbol_exists", true=True, accept={OK, UNV}, subject="new_endpoint")]))

    def b_symbol_absent(ws, rng):
        root = make_repo(ws / "repo", {"README.md": "# x\n"})
        write_file(root, "api.py", "def something_else():\n    return {}\n")
        t = TB(cwd=root)
        t.user("add the ghost_handler endpoint")
        t.write("api.py")
        t.assistant("Added the endpoint ghost_handler.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    c(_case("symbol_absent_unverifiable", "symbol not found in changed files -> UNVERIFIABLE, never FAIL",
            b_symbol_absent,
            [ef("symbol_exists", true=False, accept={UNV}, subject="ghost_handler",
                note="absence in the captured change-set is not a contradiction")]))

    return out

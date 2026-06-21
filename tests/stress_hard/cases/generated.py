"""Seeded, deterministic generators that expand the suite to 300+ cases.

Unlike the hand-written adversarial modules (which concentrate the genuine
findings), these are mostly *controls*: large matrices that exercise RedPen's
core verdict logic across many file paths, repo states, runners, and claim
phrasings, each with programmatic ground truth. They both pad the count and
catch regressions in the common path.

Generation is single-threaded and fully deterministic (no RNG): every case id,
build closure, and EF list is fixed at import time.
"""

from __future__ import annotations

from harness.builders import (TB, add_fake_upstream, commit_all, git, make_repo,
                              write_file)
from harness.model import FAIL, OK, UNV, Built, Case, ef

from ._helpers import suite_efs

AXIS = "generated"

# Paths spanning nesting, extensions, unicode, dashes, dots, deep trees.
_FILES = [
    "app.py", "main.py", "src/core.py", "src/api/routes.py", "lib/util/helpers.js",
    "lib/db/conn.ts", "pkg/mod/x.py", "pkg/mod/y.py", "a/b/c/d/e/deep.py",
    "components/Button.tsx", "styles/theme.css", "data/config.json", "docs/guide.md",
    "docs/notes.rst", "scripts/setup.sh", "internal/server.go", "crate/src/lib.rs",
    "modulo_unicode.py", "weird-name_v2.ts", "v1.2/legacy.py", "x.y.z.py",
    "handlers/webhook.py", "models/user.py", "queue/worker.py",
]


def _mk(cid, build, efs, *, tags=(), invariant=None, allow_phantom=()):
    return Case(f"{AXIS}/{cid}", AXIS, cid.split("/")[0], build, efs, tags=tags,
               invariant=invariant, allow_phantom=frozenset(allow_phantom))


# --- file_present matrix -----------------------------------------------------
def _file_cases():
    out = []
    for i, f in enumerate(_FILES):
        sub = f

        def ok_build(ws, rng, f=f):
            root = make_repo(ws / "repo", {"README.md": "# x\n"})
            write_file(root, f, "real content\nmore\n")
            t = TB(cwd=root)
            t.user(f"create {f}")
            t.write(f)
            t.assistant(f"Created {f}.")
            return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
        out.append(_mk(f"file_ok/{i:02d}", ok_build,
                       [ef("file_present", true=True, accept={OK}, subject=sub)], tags=("file",)))

        def missing_build(ws, rng, f=f):
            root = make_repo(ws / "repo", {"README.md": "# x\n"})
            t = TB(cwd=root)
            t.user(f"create {f}")
            t.assistant(f"Created {f}.")
            return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
        out.append(_mk(f"file_missing/{i:02d}", missing_build,
                       [ef("file_present", true=False, accept={FAIL}, subject=sub)], tags=("file",)))

        def pre_build(ws, rng, f=f):
            root = make_repo(ws / "repo", {"README.md": "# x\n", f: "pre-existing\n"})
            t = TB(cwd=root)
            t.user(f"create {f}")
            t.assistant(f"Created {f}.")
            return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
        out.append(_mk(f"file_preexisting/{i:02d}", pre_build,
                       [ef("file_present", true=False, accept={UNV}, subject=sub,
                           note="pre-existing, untouched -> not OK")], tags=("file",)))

        def empty_build(ws, rng, f=f):
            root = make_repo(ws / "repo", {"README.md": "# x\n"})
            write_file(root, f, "")
            t = TB(cwd=root)
            t.user(f"create {f}")
            t.write(f)
            t.assistant(f"Created {f}.")
            return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
        out.append(_mk(f"file_empty/{i:02d}", empty_build,
                       [ef("file_present", true=True, accept={UNV}, subject=sub,
                           note="created but empty -> cannot confirm content")], tags=("file",)))

        def ws_build(ws, rng, f=f):
            root = make_repo(ws / "repo", {"README.md": "# x\n"})
            write_file(root, f, "   \n\t\n  \n")
            t = TB(cwd=root)
            t.user(f"create {f}")
            t.write(f)
            t.assistant(f"Created {f}.")
            return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
        out.append(_mk(f"file_whitespace/{i:02d}", ws_build,
                       [ef("file_present", true=True, accept={UNV}, subject=sub,
                           note="whitespace-only -> cannot confirm content")], tags=("file",)))

        def mod_build(ws, rng, f=f):
            root = make_repo(ws / "repo", {"README.md": "# x\n", f: "original\n"})
            write_file(root, f, "original\nmodified this session\n")  # edited this session
            t = TB(cwd=root)
            t.user(f"update {f}")
            t.write(f, tool="Edit")
            t.assistant(f"Updated {f} with the fix.")
            return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
        out.append(_mk(f"file_modified/{i:02d}", mod_build,
                       [ef("file_present", true=True, accept={OK}, subject=sub)], tags=("file",)))

        def nongit_build(ws, rng, f=f):
            root = ws / "plain"
            root.mkdir(parents=True, exist_ok=True)
            write_file(root, f, "content here\n")
            t = TB(cwd=root)
            t.user(f"create {f}")
            t.write(f)
            t.assistant(f"Created {f}.")
            return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
        out.append(_mk(f"file_nongit/{i:02d}", nongit_build,
                       [ef("file_present", true=True, accept={OK}, subject=sub,
                           note="no git, but transcript provenance confirms it")], tags=("file", "nongit")))
    return out


# --- git state matrix --------------------------------------------------------
def _git_cases():
    out = []

    def clean_commit(ws, rng):
        root = make_repo(ws / "repo", {"app.py": "x\n"})
        t = TB(cwd=root)
        t.user("commit")
        t.assistant("Committed all the changes.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    out.append(_mk("git_clean_ok/00", clean_commit, [ef("git_clean", true=True, accept={OK})], tags=("git",)))

    def dirty_commit(ws, rng):
        root = make_repo(ws / "repo", {"app.py": "x\n"})
        write_file(root, "uncommitted.py", "y\n")
        t = TB(cwd=root)
        t.user("commit")
        t.assistant("Committed all the changes.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    out.append(_mk("git_clean_fail/00", dirty_commit,
                   [ef("git_clean", true=False, accept={FAIL})], tags=("git",)))

    def level(ws, rng):
        root = make_repo(ws / "repo", {"app.py": "x\n"})
        add_fake_upstream(root, ahead=False)
        t = TB(cwd=root)
        t.user("push")
        t.assistant("Pushed to origin.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    out.append(_mk("git_pushed_ok/00", level, [ef("git_pushed", true=True, accept={OK})], tags=("git",)))

    def ahead(ws, rng):
        root = make_repo(ws / "repo", {"app.py": "x\n"})
        add_fake_upstream(root, ahead=True)
        t = TB(cwd=root)
        t.user("push")
        t.assistant("Pushed to origin.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    out.append(_mk("git_pushed_fail/00", ahead,
                   [ef("git_pushed", true=False, accept={FAIL}, note="unpushed commit ahead of upstream")],
                   tags=("git",)))

    def no_upstream(ws, rng):
        root = make_repo(ws / "repo", {"app.py": "x\n"})
        t = TB(cwd=root)
        t.user("push")
        t.assistant("Pushed to origin.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    out.append(_mk("git_pushed_unv/00", no_upstream,
                   [ef("git_pushed", true=None, accept={UNV})], tags=("git",)))

    def synced_ok(ws, rng):
        root = make_repo(ws / "repo", {"app.py": "x\n"})
        add_fake_upstream(root, ahead=False)
        t = TB(cwd=root)
        t.user("sync")
        t.assistant("The branch is in sync with the remote.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    out.append(_mk("git_synced_ok/00", synced_ok,
                   [ef("branch_synced", true=True, accept={OK})], tags=("git",)))
    return out


# --- tests / test_count matrix ----------------------------------------------
_PYTEST_CFG = {"pyproject.toml": "[tool.pytest.ini_options]\n", "tests/test_a.py": "def test():\n    assert True\n"}
_NPM_CFG = {"package.json": '{"name":"web","scripts":{"test":"jest"}}\n'}
_CARGO_CFG = {"Cargo.toml": "[package]\nname='x'\nversion='0.1.0'\n"}
_GO_CFG = {"go.mod": "module x\n\ngo 1.21\n"}


def _test_cases():
    out = []
    # (name, repo config, command containing a recognized keyword, ok-output, bad-output)
    runners = [
        ("pytest", _PYTEST_CFG, "pytest -q", "{n} passed in 0.2s", "=== 1 failed, 2 passed in 0.2s ==="),
        ("npm", _NPM_CFG, "npm test --silent", "Tests: {n} passed, {n} total", "Tests: 1 failed, 2 passed"),
        ("cargo", _CARGO_CFG, "cargo test", "test result: ok. {n} passed; 0 failed", "test result: FAILED. 1 failed"),
        ("gotest", _GO_CFG, "go test ./...", "ok pkg {n} passed", "--- FAIL: TestX"),
    ]
    for i, (name, cfg, cmd, ok_out, bad_out) in enumerate(runners):
        out.append(_mk(f"tests_pass/{name}",
                       (lambda cfg=cfg, cmd=cmd, ok_out=ok_out: lambda ws, rng: _tx_tests(
                           ws, cmd, ok_out.format(n=5), failed=False, final="All tests pass.", files=dict(cfg)))(),
                       [ef("tests_pass", true=True, accept={OK})], tags=("tests",)))
        out.append(_mk(f"tests_fail/{name}",
                       (lambda cfg=cfg, cmd=cmd, bad_out=bad_out: lambda ws, rng: _tx_tests(
                           ws, cmd, bad_out, failed=True, final="All tests pass now.", files=dict(cfg)))(),
                       [ef("tests_pass", true=False, accept={FAIL})], tags=("tests",)))
    # no run -> UNVERIFIABLE
    out.append(_mk("tests_unv/00", lambda ws, rng: _tx_tests(ws, None, None, final="All tests pass."),
                   [ef("tests_pass", true=None, accept={UNV})], tags=("tests",)))
    # exact count correct / wrong
    out.append(_mk("test_count_ok/00",
                   lambda ws, rng: _tx_tests(ws, "pytest -q", "10 passed in 0.3s", failed=False,
                                             final="All 10 tests pass."),
                   [ef("tests_pass", true=True, accept={OK, UNV}),
                    ef("test_count", true=True, accept={OK}, subject="10")], tags=("tests",)))
    out.append(_mk("test_count_wrong/00",
                   lambda ws, rng: _tx_tests(ws, "pytest -q", "7 passed in 0.3s", failed=False,
                                             final="All 10 tests pass."),
                   [ef("tests_pass", true=True, accept={OK, UNV}),
                    ef("test_count", true=False, accept={FAIL}, subject="10",
                       note="claimed 10, run shows 7")], tags=("tests",)))
    return out


def _tx_tests(ws, cmd, out_text, *, failed=False, final, files=None):
    root = make_repo(ws / "repo", files or {"app.py": "x\n", "tests/test_a.py": "def test():\n    assert True\n",
                                            "pyproject.toml": "[tool.pytest.ini_options]\n"})
    t = TB(cwd=root)
    t.user("run the tests")
    if cmd is not None:
        t.bash(cmd, output=out_text or "", failed=failed)
    t.assistant(final)
    return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))


# --- dep_present matrix ------------------------------------------------------
def _dep_cases():
    out = []
    names = ["requests", "flask", "numpy"]
    for i, name in enumerate(names):
        def both(ws, rng, name=name):
            root = make_repo(ws / "repo", {
                "pyproject.toml": f'[project]\nname="x"\nversion="0"\ndependencies=["{name}>=1"]\n',
                "uv.lock": f'[[package]]\nname = "{name}"\nversion = "1.0"\n',
            })
            t = TB(cwd=root)
            t.user(f"add {name}")
            t.assistant(f"Added dependency {name}.")
            return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
        out.append(_mk(f"dep_ok/{name}", both, [ef("dep_present", true=True, accept={OK}, subject=name)],
                       tags=("dep",)))

        def neither(ws, rng, name=name):
            root = make_repo(ws / "repo", {"pyproject.toml": '[project]\nname="x"\nversion="0"\ndependencies=[]\n'})
            t = TB(cwd=root)
            t.user(f"add {name}")
            t.assistant(f"Added dependency {name}.")
            return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
        out.append(_mk(f"dep_fail/{name}", neither,
                       [ef("dep_present", true=False, accept={FAIL}, subject=name)], tags=("dep",)))

        def manifest_only(ws, rng, name=name):
            root = make_repo(ws / "repo", {
                "pyproject.toml": f'[project]\nname="x"\nversion="0"\ndependencies=["{name}"]\n'})
            t = TB(cwd=root)
            t.user(f"add {name}")
            t.assistant(f"Added dependency {name}.")
            return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
        out.append(_mk(f"dep_manifest_only/{name}", manifest_only,
                       [ef("dep_present", true=False, accept={UNV}, subject=name,
                           note="declared but not in a lockfile -> not installed")], tags=("dep",)))
    return out


# --- symbol_exists matrix ----------------------------------------------------
def _symbol_cases():
    out = []
    syms = ["handle_login", "UserModel", "renderChart", "parse_config", "WebhookRouter"]
    for i, s in enumerate(syms):
        def present(ws, rng, s=s):
            root = make_repo(ws / "repo", {"README.md": "# x\n"})
            write_file(root, "impl.py", f"def {s}():\n    return 1\n")
            t = TB(cwd=root)
            t.user(f"add {s}")
            t.write("impl.py")
            t.assistant(f"Added function {s}.")
            return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
        out.append(_mk(f"symbol_ok/{s}", present,
                       [ef("symbol_exists", true=True, accept={OK, UNV}, subject=s)], tags=("symbol",)))

        def absent(ws, rng, s=s):
            root = make_repo(ws / "repo", {"README.md": "# x\n"})
            write_file(root, "impl.py", "def unrelated():\n    return 0\n")
            t = TB(cwd=root)
            t.user(f"add {s}")
            t.write("impl.py")
            t.assistant(f"Added function {s}.")
            return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
        out.append(_mk(f"symbol_absent/{s}", absent,
                       [ef("symbol_exists", true=False, accept={UNV}, subject=s,
                           note="absence in the change-set is not a contradiction")], tags=("symbol",)))
    return out


# --- contradiction signature coverage (controls: real failures -> FAIL) ------
def _sig_cases():
    sigs = [
        ("pytest_summary", "pytest -q", "=== 1 failed, 2 passed in 0.2s ===", "tests"),
        ("pytest_nodeid", "pytest -q", "FAILED tests/test_a.py::test_x", "tests"),
        ("assertion", "pytest -q", "E   AssertionError: expected 1 got 2", "tests"),
        ("jest_failing", "npx jest", "3 failing", "tests"),
        ("cargo_failed", "cargo test", "test result: FAILED. 1 failed", "tests"),
        ("build_failed", "npm run build", "BUILD FAILED: oops", "build"),
        ("tsc_error", "npm run build", "src/x.ts(1,1): error TS2304: cannot find name", "build"),
        ("rustc_error", "cargo build", "error[E0382]: borrow of moved value", "build"),
        ("traceback", "python run.py", "Traceback (most recent call last):\n  File x\nValueError", "any"),
        ("generic_error", "make all", "error: linker failed", "any"),
    ]
    out = []
    for name, cmd, sig, kind in sigs:
        if kind == "tests":
            probe, final = "tests_pass", "All tests pass now."
        elif kind == "build":
            probe, final = "build_ok", "The build succeeds now."
        else:
            probe, final = "contradiction_scan", "All done, everything works."

        def build(ws, rng, cmd=cmd, sig=sig, final=final):
            root = make_repo(ws / "repo", {"app.py": "x\n"})
            t = TB(cwd=root)
            t.user("fix it")
            t.bash(cmd, output=sig, failed=True)
            t.assistant(final)
            return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
        if probe == "contradiction_scan":
            efs = suite_efs({"contradiction_scan": ef("contradiction_scan", true=False, accept={FAIL}),
                             "git_clean": ef("git_clean", true=True, accept={OK})})
        else:
            efs = [ef(probe, true=False, accept={FAIL})]
        out.append(_mk(f"sig_real/{name}", build, efs, tags=("contradiction", "control")))
    return out


# --- typecheck matrix --------------------------------------------------------
def _typecheck_cases():
    out = []

    def clean(ws, rng):
        root = make_repo(ws / "repo", {"pyproject.toml": "[tool.mypy]\nstrict=true\n", "app.py": "x=1\n"})
        t = TB(cwd=root)
        t.user("typecheck")
        t.bash("mypy .", output="Success: no issues found in 1 source file", failed=False)
        t.assistant("mypy is clean, no type errors.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    out.append(_mk("typecheck_ok/00", clean, [ef("typecheck_clean", true=True, accept={OK})],
                   tags=("typecheck",)))

    def bad(ws, rng):
        root = make_repo(ws / "repo", {"pyproject.toml": "[tool.mypy]\n", "app.py": "x=1\n"})
        t = TB(cwd=root)
        t.user("typecheck")
        t.bash("mypy .", output="app.py:1: error: Incompatible types\nFound 1 error in 1 file", failed=True)
        t.assistant("Types are clean now, no type errors.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    out.append(_mk("typecheck_fail/00", bad, [ef("typecheck_clean", true=False, accept={FAIL})],
                   tags=("typecheck",)))

    def notrun(ws, rng):
        root = make_repo(ws / "repo", {"pyproject.toml": "[tool.mypy]\n", "app.py": "x=1\n"})
        t = TB(cwd=root)
        t.user("typecheck")
        t.assistant("mypy is clean, no type errors.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    out.append(_mk("typecheck_unv/00", notrun, [ef("typecheck_clean", true=None, accept={UNV})],
                   tags=("typecheck",)))
    return out


# --- phrasing matrix (single safe probe per phrasing) ------------------------
def _phrasing_cases():
    # (cid, final, probe, reality, accept)
    rows = [
        ("push_unv", "Pushed to origin.", "git_pushed", None, {UNV}),
        ("commit_ok", "Committed all the changes.", "git_clean", True, {OK}),
        ("compile_unv", "The code compiles cleanly.", "build_ok", None, {UNV}),
        ("lint_unv", "The linter is clean now.", "lint_clean", None, {UNV}),
        ("refactor_unmapped", "Refactored the whole parser.", "unmapped", None, {UNV}),
        ("fixed_unmapped", "Fixed the off-by-one bug.", "unmapped", None, {UNV}),
        ("implemented_unmapped", "Implemented the retry logic.", "unmapped", None, {UNV}),
        ("integrated_unmapped", "Integrated the payment provider.", "unmapped", None, {UNV}),
    ]
    out = []
    for cid, final, probe, reality, accept in rows:
        def build(ws, rng, final=final):
            root = make_repo(ws / "repo", {"README.md": "# x\n"})
            t = TB(cwd=root)
            t.user("do the work")
            t.assistant(final)
            return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
        out.append(_mk(f"phrasing/{cid}", build,
                       [ef(probe, true=reality, accept=accept)], tags=("phrasing",)))
    return out


def cases():
    out = []
    out += _file_cases()
    out += _git_cases()
    out += _test_cases()
    out += _dep_cases()
    out += _symbol_cases()
    out += _sig_cases()
    out += _typecheck_cases()
    out += _phrasing_cases()
    return out

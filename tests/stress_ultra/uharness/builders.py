"""Seeded, reproducible builders: RNG, git/filesystem helpers, and a transcript
JSONL builder whose output exactly matches what redpen.transcript.parse_transcript
consumes (validated empirically against a live `redpen check`).

Determinism contract: every case is generated once, single-threaded, from a
fixed master seed (see cases/registry.py). Build closures use a per-case RNG for
*noise only*; the signal (claims, the files that matter) is baked into the
closure at generation time, so ground truth never drifts and the suite is safe
under pytest-xdist (process-level parallelism, serial within a worker).
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]

# --- git environment: fully isolated from the user's global/system config -----
_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Stress",
    "GIT_AUTHOR_EMAIL": "stress@example.com",
    "GIT_COMMITTER_NAME": "Stress",
    "GIT_COMMITTER_EMAIL": "stress@example.com",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "/usr/bin/true",
}


def git_env() -> dict:
    return {**os.environ, **_GIT_ENV}


def git(cwd, *args, check: bool = False, timeout: float = 30) -> tuple[int, str, str]:
    """Run a git command in ``cwd``; returns (rc, stdout, stderr)."""
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), env=git_env(),
        capture_output=True, text=True, timeout=timeout,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc.returncode, proc.stdout, proc.stderr


def git_init(cwd, branch: str = "main") -> None:
    git(cwd, "init", "-b", branch, check=True)


def commit_all(cwd, msg: str = "c") -> None:
    git(cwd, "add", "-A", check=True)
    git(cwd, "-c", "commit.gpgsign=false", "commit", "-m", msg, check=True)


def write_file(root, rel: str, content: str = "x\n") -> Path:
    """Write ``content`` to root/rel, creating parent dirs. Returns the path."""
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


# --- seeded RNG ---------------------------------------------------------------
_VOCAB = (
    "alpha beta gamma delta core util parser engine render ledger probe claim "
    "token handler config schema worker queue cache router module service client "
    "session commit branch verdict scan baseline audit signal evidence finding "
    "build deploy refactor migrate validate resolve compute stream batch index "
    "the quick brown fox jumps over a lazy dog while logging output to console"
).split()


class Rng:
    """Fully instance-local deterministic randomness (no global state), so the
    suite is reproducible under any parallelism."""

    def __init__(self, seed: int):
        self.seed = seed
        self.r = random.Random(seed)

    def choice(self, seq):
        return self.r.choice(list(seq))

    def randint(self, a, b):
        return self.r.randint(a, b)

    def chance(self, p: float) -> bool:
        return self.r.random() < p

    def words(self, n: int = 4) -> str:
        return " ".join(self.r.choice(_VOCAB) for _ in range(n))

    def sentence(self) -> str:
        n = self.r.randint(5, 11)
        s = " ".join(self.r.choice(_VOCAB) for _ in range(n))
        return s[0].upper() + s[1:] + "."

    def slug(self) -> str:
        return "-".join(self.r.choice(_VOCAB) for _ in range(2))

    def filename(self, ext: str = ".py") -> str:
        return self.r.choice(_VOCAB) + ext


# --- transcript builder -------------------------------------------------------
class TB:
    """Build a Claude Code transcript JSONL line-by-line.

    The LAST assistant text block becomes ``final_assistant_text`` (the claim
    source); the LAST genuine user turn becomes ``final_user_text``. Tool events
    are produced from a tool_use line plus a matching tool_result line.
    """

    def __init__(self, session_id: str = "sess-stress", cwd=None, entrypoint: str = "cli"):
        self.session_id = session_id
        self.cwd = str(cwd) if cwd is not None else None
        self.entrypoint = entrypoint
        self._lines: list = []  # dicts, or ("__raw__", str)
        self._tid = 0

    def _base(self) -> dict:
        d: dict = {}
        if self.session_id:
            d["sessionId"] = self.session_id
        if self.cwd:
            d["cwd"] = self.cwd
        if self.entrypoint:
            d["entrypoint"] = self.entrypoint
        return d

    def _new_tid(self) -> str:
        self._tid += 1
        return f"toolu_{self._tid:04d}"

    # -- turns -----------------------------------------------------------------
    def user(self, text: str, *, meta: bool = False, sidechain: bool = False) -> "TB":
        line = self._base()
        line["type"] = "user"
        line["message"] = {"role": "user", "content": text}
        if meta:
            line["isMeta"] = True
        if sidechain:
            line["isSidechain"] = True
        self._lines.append(line)
        return self

    def assistant(self, text: str, *, sidechain: bool = False) -> "TB":
        line = self._base()
        line["type"] = "assistant"
        line["message"] = {"role": "assistant", "content": [{"type": "text", "text": text}]}
        if sidechain:
            line["isSidechain"] = True
        self._lines.append(line)
        return self

    def tool_use(self, tool: str, inp: dict, *, tid: str | None = None, sidechain: bool = False) -> str:
        tid = tid or self._new_tid()
        line = self._base()
        line["type"] = "assistant"
        line["message"] = {"role": "assistant",
                           "content": [{"type": "tool_use", "name": tool, "id": tid, "input": inp}]}
        if sidechain:
            line["isSidechain"] = True
        self._lines.append(line)
        return tid

    def tool_result(self, tid: str, *, content: str = "", is_error: bool = False,
                    exit_code=None, success=None, sidechain: bool = False) -> "TB":
        line = self._base()
        line["type"] = "user"
        block = {"type": "tool_result", "tool_use_id": tid, "content": content}
        if is_error:
            block["is_error"] = True
        line["message"] = {"role": "user", "content": [block]}
        tur: dict = {}
        if exit_code is not None:
            tur["exitCode"] = exit_code
        if success is not None:
            tur["success"] = success
        if tur:
            tur.setdefault("stdout", content if isinstance(content, str) else "")
            line["toolUseResult"] = tur
        if sidechain:
            line["isSidechain"] = True
        self._lines.append(line)
        return self

    # -- convenience -----------------------------------------------------------
    def bash(self, command: str, *, output: str = "", failed: bool = False,
             exit_code=None, sidechain: bool = False) -> "TB":
        tid = self.tool_use("Bash", {"command": command}, sidechain=sidechain)
        ec = exit_code if exit_code is not None else (1 if failed else 0)
        self.tool_result(tid, content=output, is_error=failed, exit_code=ec,
                         success=(not failed), sidechain=sidechain)
        return self

    def write(self, file_path: str, *, tool: str = "Write", sidechain: bool = False) -> "TB":
        key = "notebook_path" if tool == "NotebookEdit" else "file_path"
        self.tool_use(tool, {key: file_path}, sidechain=sidechain)
        return self

    def raw(self, raw_line: str) -> "TB":
        self._lines.append(("__raw__", raw_line))
        return self

    def noise_bash(self, rng: Rng, n: int, *, big: bool = False) -> "TB":
        """Append n innocuous, passing bash tool calls (transcript noise)."""
        verbs = ["ls -la", "cat README.md", "grep -rn TODO src", "git status",
                 "echo building", "pwd", "wc -l *.py", "head -n 20 log.txt",
                 "find . -name '*.py'", "python -c 'print(1)'"]
        for _ in range(n):
            cmd = rng.choice(verbs)
            out = rng.sentence()
            if big:
                out = "\n".join(rng.sentence() for _ in range(rng.randint(20, 60)))
            self.bash(cmd, output=out, failed=False)
        return self

    # -- serialize -------------------------------------------------------------
    def to_jsonl(self) -> str:
        parts = []
        for ln in self._lines:
            if isinstance(ln, tuple) and ln[0] == "__raw__":
                parts.append(ln[1])
            else:
                parts.append(json.dumps(ln))
        return "\n".join(parts) + "\n"

    def write_to(self, path, *, truncate_last: bool = False, trailing_garbage: bool = False) -> Path:
        path = Path(path)
        text = self.to_jsonl()
        if trailing_garbage:
            text = text + '{"type":"assistant","message":{"content":[{"type":"text","text":"part'
        if truncate_last:
            # Chop the final newline and half the last line -> invalid trailing JSON.
            text = text.rstrip("\n")
            cut = text.rfind("\n")
            if cut != -1:
                last = text[cut + 1:]
                text = text[:cut + 1] + last[: max(1, len(last) // 2)]
        path.write_text(text, encoding="utf-8")
        return path


# --- common repo scenarios ----------------------------------------------------
def make_repo(
    root,
    files: dict | None = None,
    *,
    commit: bool = True,
    branch: str = "main",
    gitignore_redpen: bool = True,
) -> Path:
    """Init a git repo at root with optional files; commit them by default.

    By default a ``.gitignore`` with ``.redpen/`` is written and committed --
    exactly what a real RedPen user has, and necessary so RedPen's own state
    writes (ledger, judge cache) never make a subsequent ``git status`` dirty.
    Pass gitignore_redpen=False for the explicit "user never gitignored .redpen"
    scenario.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    git_init(root, branch=branch)
    files = dict(files or {})
    if gitignore_redpen and ".gitignore" not in files:
        files[".gitignore"] = ".redpen/\n"
    for rel, content in files.items():
        write_file(root, rel, content)
    if commit and files:
        commit_all(root, "init")
    return root


def redpen_baseline(root) -> None:
    """Run `redpen baseline` to snapshot task-start state (HEAD + status + fs)."""
    env = {**os.environ, "PYTHONPATH": str(_REPO_ROOT),
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
    subprocess.run(
        [sys.executable, "-m", "redpen.cli", "baseline", "--quiet"],
        cwd=str(root), env=env, capture_output=True, text=True, timeout=30,
    )


def add_fake_upstream(root, *, ahead: bool = False) -> None:
    """Give the repo an upstream by cloning into a bare 'remote' and tracking it.

    With ahead=True, a local commit is added after wiring the upstream, so HEAD
    is ahead of @{u} (an unpushed commit).
    """
    root = Path(root)
    remote = root.parent / (root.name + "_remote.git")
    git(root, "clone", "--bare", str(root), str(remote), check=True)
    git(root, "remote", "add", "origin", str(remote), check=True)
    git(root, "fetch", "origin", check=True)
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD")[1].strip()
    git(root, "branch", "--set-upstream-to", f"origin/{branch}", check=True)
    if ahead:
        write_file(root, "extra.txt", "more\n")
        commit_all(root, "ahead commit")


# ============================================================================
# ULTRA additions: hostile git states, huge/encoding-edge transcripts, and
# shell-context command builders for the stress_ultra suite.
# ============================================================================

import os as _os

_PROTO = ("-c", "protocol.file.allow=always")  # allow local-path submodules


def detach_head(root) -> None:
    """Put the repo in a detached-HEAD state at the current commit."""
    sha = git(root, "rev-parse", "HEAD")[1].strip()
    git(root, "checkout", "--detach", sha)


def start_merge_conflict(root, branch: str = "main") -> None:
    """Leave the repo mid-merge with an unresolved conflict (MERGE_HEAD present)."""
    git(root, "checkout", "-b", "feature")
    write_file(root, "conflict.txt", "feature side\n")
    commit_all(root, "feature change")
    git(root, "checkout", branch)
    write_file(root, "conflict.txt", "main side\n")
    commit_all(root, "main change")
    git(root, "merge", "feature")  # conflicts -> leaves MERGE_HEAD + unmerged paths


def start_rebase_conflict(root, branch: str = "main") -> None:
    """Leave the repo mid-rebase with a conflict (rebase-merge/apply present)."""
    git(root, "checkout", "-b", "topic")
    write_file(root, "r.txt", "topic side\n")
    commit_all(root, "topic change")
    git(root, "checkout", branch)
    write_file(root, "r.txt", "main side\n")
    commit_all(root, "main change")
    git(root, "checkout", "topic")
    git(root, "rebase", branch)  # conflicts -> leaves rebase state


def start_cherrypick_conflict(root, branch: str = "main") -> None:
    """Leave the repo mid-cherry-pick with a conflict (CHERRY_PICK_HEAD present)."""
    git(root, "checkout", "-b", "side")
    write_file(root, "cp.txt", "side side\n")
    commit_all(root, "side change")
    sha = git(root, "rev-parse", "HEAD")[1].strip()
    git(root, "checkout", branch)
    write_file(root, "cp.txt", "main side\n")
    commit_all(root, "main change")
    git(root, "cherry-pick", sha)  # conflicts -> leaves CHERRY_PICK_HEAD


def add_submodule(root, name: str = "vendor/lib") -> Path:
    """Add a local-path submodule; returns the submodule working dir."""
    sub_origin = Path(root).parent / (Path(root).name + "_sub.git")
    sub_origin.mkdir(parents=True, exist_ok=True)
    git_init(sub_origin)
    write_file(sub_origin, "mod.py", "def mod():\n    return 1\n")
    commit_all(sub_origin, "sub init")
    git(root, *_PROTO, "submodule", "add", str(sub_origin), name)
    commit_all(root, "add submodule")
    return Path(root) / name


def add_worktree(root, branch: str = "wt") -> Path:
    """Create a linked worktree on a new branch; returns its path."""
    wt = Path(root).parent / (Path(root).name + "_wt")
    git(root, "worktree", "add", "-b", branch, str(wt))
    return wt


# --- huge / pathological transcripts ----------------------------------------
_NOISE_CMDS = ["ls -la", "grep -rn TODO src", "cat README.md", "echo building",
               "pwd", "wc -l *.py", "head -n 20 log.txt", "find . -name '*.py'",
               "git status", "python -c 'print(1)'", "node -e 'console.log(1)'"]


def huge_noise(tb: "TB", rng: "Rng", *, turns: int = 320, lines: int = 40) -> "TB":
    """Append many innocuous, passing turns -- tens to hundreds of thousands of
    tokens of pure noise the extractor must ignore."""
    for _ in range(turns):
        tb.assistant(rng.sentence())
        tb.bash(rng.choice(_NOISE_CMDS),
                output="\n".join(rng.sentence() for _ in range(lines)), failed=False)
    return tb


def approx_tokens(path: Path) -> int:
    try:
        return Path(path).stat().st_size // 4  # ~4 chars/token, good enough for a budget check
    except OSError:
        return 0


# --- encoding edges ----------------------------------------------------------
def write_jsonl_bytes(tb: "TB", path, *, bom: bool = False, crlf: bool = False,
                      trailing_garbage: bool = False) -> Path:
    """Serialize a transcript with byte-level edges: UTF-8 BOM, CRLF lines, or a
    truncated trailing object. parse_transcript must survive all of them."""
    text = tb.to_jsonl()
    if trailing_garbage:
        text += '{"type":"assistant","message":{"content":[{"type":"text","text":"part'
    data = text.replace("\n", "\r\n").encode("utf-8") if crlf else text.encode("utf-8")
    if bom:
        data = b"\xef\xbb\xbf" + data
    Path(path).write_bytes(data)
    return Path(path)


# Invisible / bidi / wide characters to smuggle into claims and filenames.
ZWSP = "​"      # zero-width space
RLO = "‮"       # right-to-left override
LRI = "⁦"       # left-to-right isolate
NBSP = " "      # non-breaking space


# --- shell-context command wrappers (dimension D) ---------------------------
def heredoc(body: str, *, delim: str = "EOF") -> str:
    return f"cat <<'{delim}'\n{body}\n{delim}"

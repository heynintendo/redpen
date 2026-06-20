"""Custom probe config: .redpen.yml.

A list of rules mapping a ``claim_pattern`` (regex) to a ``command`` and an
expected ``exit`` and/or ``output``. A claim hits the FIRST matching rule. The
rule's command runs in a subprocess with a timeout; the verdict comes from
comparing exit/output. Anything ambiguous (couldn't run, timed out, partial
match) is UNVERIFIABLE -- never a guessed FAIL.

Because rules run user-defined commands, execution is gated: a rule may declare
``safe: true`` to run on the normal path; rules without it run only under --run.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import _miniyaml

CONFIG_NAMES = (".redpen.yml", ".redpen.yaml", ".redpen.json")
_DEFAULT_TIMEOUT = 10


@dataclass
class Rule:
    claim_pattern: str
    command: str
    expect_exit: int | None = None
    expect_output: str | None = None
    expect_output_regex: bool = False
    safe: bool = False
    timeout: int = _DEFAULT_TIMEOUT
    name: str = ""

    def as_dict(self) -> dict:
        return {
            "claim_pattern": self.claim_pattern,
            "command": self.command,
            "expect_exit": self.expect_exit,
            "expect_output": self.expect_output,
            "expect_output_regex": self.expect_output_regex,
            "safe": self.safe,
            "timeout": self.timeout,
            "name": self.name,
        }


def config_path(project_root: Path | str) -> Path | None:
    root = Path(project_root)
    for name in CONFIG_NAMES:
        p = root / name
        if p.is_file():
            return p
    return None


def _coerce(raw: dict) -> Rule | None:
    pattern = raw.get("claim_pattern")
    command = raw.get("command")
    if not pattern or not command:
        return None
    try:
        re.compile(str(pattern))  # validate the regex up front
    except re.error:
        return None
    return Rule(
        claim_pattern=str(pattern),
        command=str(command),
        expect_exit=raw.get("expect_exit"),
        expect_output=(str(raw["expect_output"]) if raw.get("expect_output") is not None else None),
        expect_output_regex=bool(raw.get("expect_output_regex", False)),
        safe=bool(raw.get("safe", False)),
        timeout=int(raw.get("timeout", _DEFAULT_TIMEOUT)),
        name=str(raw.get("name", "")),
    )


def load_rules(project_root: Path | str) -> list[Rule]:
    """Load and validate rules from .redpen.yml/.yaml/.json (empty on any problem)."""
    path = config_path(project_root)
    if path is None:
        return []
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text) if path.suffix == ".json" else _miniyaml.parse(text)
    except (OSError, ValueError):
        return []
    rules = (data or {}).get("rules", []) if isinstance(data, dict) else []
    out: list[Rule] = []
    for raw in rules if isinstance(rules, list) else []:
        if isinstance(raw, dict):
            rule = _coerce(raw)
            if rule is not None:
                out.append(rule)
    return out


def match_rule(rules: list[Rule], claim_text: str) -> Rule | None:
    """The first rule whose claim_pattern matches the claim text."""
    for rule in rules:
        try:
            if re.search(rule.claim_pattern, claim_text, re.IGNORECASE):
                return rule
        except re.error:
            continue
    return None


def run_rule(rule: Rule, cwd: Path | str) -> tuple[int, str]:
    """Execute the rule's command. Returns (exit_code, combined_output).

    exit_code is 127 for a failed launch, 124 for a timeout (so callers can map
    those to UNVERIFIABLE rather than FAIL).
    """
    try:
        proc = subprocess.run(
            rule.command, shell=True, cwd=str(cwd), capture_output=True,
            text=True, timeout=rule.timeout,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except OSError:
        return 127, "could not launch command"

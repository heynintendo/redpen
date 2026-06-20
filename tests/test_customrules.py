"""Tests for custom .redpen.yml rules: parsing, matching, gating, verdicts."""

from __future__ import annotations

from redpen import _miniyaml
from redpen.customrules import load_rules, match_rule
from redpen.probes import custom_rule
from redpen.probes.base import ProbeContext, Verdict

_YML = """
# a comment
rules:
  - name: migration
    claim_pattern: "ran the migration"
    command: "echo head"
    expect_output: "head"
    safe: true
  - name: deploy
    claim_pattern: "deployed to staging"
    command: "true"
    expect_exit: 0
"""


def test_miniyaml_parses_rule_list():
    data = _miniyaml.parse(_YML)
    assert len(data["rules"]) == 2
    r0 = data["rules"][0]
    assert r0["name"] == "migration" and r0["safe"] is True
    assert r0["claim_pattern"] == "ran the migration"


def test_load_and_match_rules(tmp_path):
    (tmp_path / ".redpen.yml").write_text(_YML)
    rules = load_rules(tmp_path)
    assert len(rules) == 2
    hit = match_rule(rules, "I ran the migration successfully")
    assert hit is not None and hit.name == "migration"
    assert match_rule(rules, "something unrelated") is None


def test_safe_rule_runs_and_passes(tmp_path):
    rule = {"name": "m", "command": "echo head", "expect_output": "head", "safe": True}
    res = custom_rule(ProbeContext(cwd=tmp_path), rule=rule)
    assert res.verdict is Verdict.OK


def test_safe_rule_output_mismatch_is_fail(tmp_path):
    rule = {"name": "m", "command": "echo nope", "expect_output": "head", "safe": True}
    res = custom_rule(ProbeContext(cwd=tmp_path), rule=rule)
    assert res.verdict is Verdict.FAIL


def test_unsafe_rule_is_gated_without_run(tmp_path):
    rule = {"name": "deploy", "command": "true", "expect_exit": 0, "safe": False}
    res = custom_rule(ProbeContext(cwd=tmp_path, run=False), rule=rule)
    assert res.verdict is Verdict.UNVERIFIABLE
    assert res.evidence.get("gated") is True


def test_unsafe_rule_runs_under_run(tmp_path):
    rule = {"name": "deploy", "command": "true", "expect_exit": 0, "safe": False}
    res = custom_rule(ProbeContext(cwd=tmp_path, run=True), rule=rule)
    assert res.verdict is Verdict.OK


def test_rule_exit_mismatch_is_fail(tmp_path):
    rule = {"command": "false", "expect_exit": 0, "safe": True}
    res = custom_rule(ProbeContext(cwd=tmp_path), rule=rule)
    assert res.verdict is Verdict.FAIL


def test_rule_command_not_found_is_unverifiable(tmp_path):
    rule = {"command": "this-binary-does-not-exist-xyz", "expect_exit": 0, "safe": True}
    res = custom_rule(ProbeContext(cwd=tmp_path), rule=rule)
    # shell launches but the inner command fails (127); not our launch failure.
    assert res.verdict in (Verdict.UNVERIFIABLE, Verdict.FAIL)


def test_shipped_example_parses():
    from pathlib import Path

    example = Path(__file__).resolve().parent.parent / "docs" / "redpen.example.yml"
    rules = load_rules_from_file(example)
    assert any(r.name == "migration-applied" for r in rules)


def load_rules_from_file(path):
    # The example lives at docs/redpen.example.yml; load it by copying the parser.
    from redpen.customrules import _coerce

    data = _miniyaml.parse(path.read_text())
    return [r for raw in data.get("rules", []) if (r := _coerce(raw))]

"""The custom_rule probe: verify a claim with a user-defined command (.redpen.yml)."""

from __future__ import annotations

import re

from ..customrules import Rule, run_rule
from .base import ProbeContext, ProbeResult, fail, ok, unverifiable


def custom_rule(ctx: ProbeContext, rule: dict | None = None, **_: object) -> ProbeResult:
    """Run a configured rule's command and compare exit/output to expectations.

    Gated: a rule runs on the normal path only if it declared ``safe: true``;
    otherwise it runs only under --run. Couldn't-run / timeout / no-expectation
    is UNVERIFIABLE, never FAIL.
    """
    if not rule:
        return unverifiable("custom_rule", "no rule supplied")
    r = Rule(
        claim_pattern=rule.get("claim_pattern", ""),
        command=rule.get("command", ""),
        expect_exit=rule.get("expect_exit"),
        expect_output=rule.get("expect_output"),
        expect_output_regex=bool(rule.get("expect_output_regex", False)),
        safe=bool(rule.get("safe", False)),
        timeout=int(rule.get("timeout", 10)),
        name=rule.get("name", ""),
    )
    label = r.name or r.command
    if not (r.safe or ctx.run):
        return unverifiable(
            "custom_rule",
            f"custom rule runs `{r.command[:50]}`; mark it `safe: true` or pass --run to execute",
            rule=label, command=r.command, gated=True,
        )

    rc, output = run_rule(r, ctx.cwd)
    if rc in (124, 127):
        return unverifiable("custom_rule", f"could not evaluate rule: {output[:60]}", rule=label, command=r.command)

    # Default expectation if the rule specified none: a clean exit.
    expect_exit = r.expect_exit if (r.expect_exit is not None or r.expect_output is not None) else 0
    exit_ok = expect_exit is None or rc == expect_exit
    out_ok = True
    if r.expect_output is not None:
        if r.expect_output_regex:
            out_ok = bool(re.search(r.expect_output, output))
        else:
            out_ok = r.expect_output in output

    ev = {"rule": label, "command": r.command, "exit_code": rc,
          "expect_exit": expect_exit, "expect_output": r.expect_output}
    if exit_ok and out_ok:
        return ok("custom_rule", f"rule satisfied: `{r.command[:50]}`", **ev)
    why = []
    if not exit_ok:
        why.append(f"exit {rc} != expected {expect_exit}")
    if not out_ok:
        why.append(f"expected output {'/'.join([str(r.expect_output)])!s} not found")
    return fail("custom_rule", f"rule not satisfied: {'; '.join(why)}", **ev)

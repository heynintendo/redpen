"""Tests for the opt-in auto-verify Stop hook (install/uninstall)."""

from __future__ import annotations

import json

from redpen.hook import HOOK_COMMAND, install_hook, settings_path, uninstall_hook


def _settings(root):
    return json.loads(settings_path(root).read_text())


def test_install_writes_deterministic_stop_hook(tmp_path):
    changed, info = install_hook(tmp_path)
    assert changed is True
    assert info == str(settings_path(tmp_path))

    data = _settings(tmp_path)
    cmds = [h["command"] for g in data["hooks"]["Stop"] for h in g["hooks"]]
    assert HOOK_COMMAND in cmds
    # Deterministic only: the hook must NEVER use --deep.
    assert all("--deep" not in c for c in cmds)
    # Recursion guard env is set on the command.
    assert "REDPEN_HOOK=1" in HOOK_COMMAND


def test_install_is_idempotent(tmp_path):
    install_hook(tmp_path)
    changed, info = install_hook(tmp_path)
    assert changed is False
    assert info == "already installed"
    data = _settings(tmp_path)
    cmds = [h["command"] for g in data["hooks"]["Stop"] for h in g["hooks"]]
    assert cmds.count(HOOK_COMMAND) == 1  # not duplicated


def test_uninstall_is_fully_reversible_and_preserves_other_settings(tmp_path):
    # Pre-existing user settings + an unrelated Stop hook must survive.
    path = settings_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "model": "opus",
                "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo keep-me"}]}]},
            }
        )
    )

    install_hook(tmp_path)
    changed, _ = uninstall_hook(tmp_path)
    assert changed is True

    data = _settings(tmp_path)
    assert data["model"] == "opus"  # unrelated setting preserved
    cmds = [h["command"] for g in data["hooks"]["Stop"] for h in g["hooks"]]
    assert "echo keep-me" in cmds  # unrelated hook preserved
    assert all("redpen check" not in c for c in cmds)  # ours is gone


def test_uninstall_when_not_installed(tmp_path):
    changed, info = uninstall_hook(tmp_path)
    assert changed is False
    assert info == "no settings file"


def test_hook_env_disables_deep(monkeypatch, tmp_path):
    """With REDPEN_HOOK set, `check --deep` must not engage the LLM judge."""
    from redpen.cli import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REDPEN_HOOK", "1")
    # If --deep tried to spawn the judge this would error/network; instead it
    # must run deterministic-only and exit cleanly.
    rc = main(["check", "I refactored the module", "--deep", "--no-art", "--no-color"])
    assert rc == 0

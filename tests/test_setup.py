"""Terminal onboarding never leaks keys, partially saves, or blocks launchd."""
from __future__ import annotations

import importlib
import os

import pytest

from gloss import appctl
from gloss.config import config_path, load_config, save_config, set_config_value

commands = importlib.import_module("gloss.cli")


@pytest.fixture
def terminal(home, monkeypatch):
    monkeypatch.delenv("GLOSS_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(commands, "_interactive_terminal", lambda: True)
    monkeypatch.setattr(commands, "_require_darwin_app", lambda: None)
    monkeypatch.setattr(appctl, "running_instance", lambda: None)
    launches = []

    def spawn(**kwargs):
        launches.append(load_config())
        return 123

    monkeypatch.setattr(appctl, "spawn_background", spawn)
    return launches


def test_first_launch_saves_before_spawn_without_echoing_key(runner, terminal):
    result = runner.invoke(commands.cli, ["app"], input="y\nhttps://example.invalid/v1\ntest-model\nsecret-for-test\n")
    assert result.exit_code == 0, result.output
    assert "secret-for-test" not in result.output
    assert terminal[0]["llm"]["api_key"] == "secret-for-test"
    assert terminal[0]["llm"]["model"] == "test-model"
    assert terminal[0]["app"]["setup_completed"] is True
    if os.name != "nt":
        assert config_path().stat().st_mode & 0o777 == 0o600
    again = runner.invoke(commands.cli, ["app"])
    assert again.exit_code == 0
    assert "现在配置" not in again.output


def test_skip_is_remembered_and_explicit_setup_still_works(runner, terminal):
    result = runner.invoke(commands.cli, ["app"], input="n\n")
    assert result.exit_code == 0, result.output
    assert terminal[0]["llm"]["api_key"] == ""
    assert terminal[0]["app"]["setup_completed"] is True
    result = runner.invoke(commands.cli, ["app"])
    assert result.exit_code == 0
    assert "现在配置" not in result.output
    result = runner.invoke(commands.cli, ["setup"], input="\n\nsecret-for-test\n")
    assert result.exit_code == 0, result.output
    assert load_config()["llm"]["api_key"] == "secret-for-test"


def test_cancel_keeps_original_config_and_does_not_spawn(runner, terminal):
    set_config_value("app.hotkey", "ctrl+1")
    before = config_path().read_bytes()
    result = runner.invoke(commands.cli, ["app"], input="y\nhttps://example.invalid/v1\n")
    assert result.exit_code != 0
    assert config_path().read_bytes() == before
    assert terminal == []


@pytest.mark.parametrize("mode", ["hidden", "noninteractive", "configured", "environment", "running"])
def test_existing_and_unattended_launches_never_prompt(runner, terminal, monkeypatch, mode):
    args = ["app"]
    if mode == "hidden":
        args.append("--hidden")
    elif mode == "noninteractive":
        monkeypatch.setattr(commands, "_interactive_terminal", lambda: False)
    elif mode == "configured":
        set_config_value("llm.api_key", "existing-key")
    elif mode == "environment":
        monkeypatch.setenv("GLOSS_API_KEY", "environment-test-key")
    else:
        monkeypatch.setattr(appctl, "running_instance", lambda: 123)
    result = runner.invoke(commands.cli, args)
    assert result.exit_code == 0, result.output
    assert "现在配置" not in result.output


def test_explicit_setup_preserves_unrelated_fields_and_existing_key(runner, terminal):
    set_config_value("llm.api_key", "existing-secret")
    set_config_value("app.hotkey", "ctrl+1")
    set_config_value("llm.vision_model", "vision-test")
    result = runner.invoke(commands.cli, ["setup"], input="\nnew-model\n\n")
    assert result.exit_code == 0, result.output
    cfg = load_config()
    assert cfg["llm"]["api_key"] == "existing-secret"
    assert cfg["app"]["hotkey"] == "ctrl+1"
    assert cfg["llm"]["vision_model"] == "vision-test"
    assert "existing-secret" not in result.output
    assert not terminal


def test_setup_retries_invalid_address_and_model(runner, terminal):
    result = runner.invoke(commands.cli, ["setup"], input="bad-url\nhttps://example.invalid/v1\n   \nmodel\nsecret-test\n")
    assert result.exit_code == 0, result.output
    assert "完整" in result.output and "不能为空" in result.output
    assert load_config()["llm"]["model"] == "model"
    assert "secret-test" not in result.output


def test_noninteractive_setup_is_rejected_without_writing(runner, terminal, monkeypatch):
    monkeypatch.setattr(commands, "_interactive_terminal", lambda: False)
    result = runner.invoke(commands.cli, ["setup"])
    assert result.exit_code != 0
    assert not config_path().exists()


def test_atomic_config_failure_keeps_previous_file(home, monkeypatch):
    set_config_value("llm.api_key", "previous-secret")
    before = config_path().read_bytes()
    cfg = load_config()
    cfg["llm"]["api_key"] = "replacement-secret"

    def failed_replace(*args):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("gloss.config.os.replace", failed_replace)
    with pytest.raises(OSError, match="simulated"):
        save_config(cfg)
    assert config_path().read_bytes() == before
    assert list(config_path().parent.iterdir()) == [config_path()]

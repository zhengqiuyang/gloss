"""gloss app shell: importable without pyobjc, friendly error when missing."""

from __future__ import annotations

import sys

import pytest

from gloss.models import GlossError


def test_run_app_without_pyobjc_gives_install_hint(runner_env, monkeypatch):
    from gloss import app

    for mod in ("AppKit", "Foundation", "WebKit"):
        monkeypatch.setitem(sys.modules, mod, None)  # import raises ImportError

    with pytest.raises(GlossError) as exc:
        app.run_app()
    assert "pyobjc" in str(exc.value)
    assert "gloss-cli\\[app]" in str(exc.value)


def test_register_hotkey_returns_bool():
    from gloss import app

    # On macOS this registers a real hotkey and returns True; on CI/Linux
    # Carbon isn't loadable and it must return False — either way, no crash.
    result = app.register_hotkey(lambda: None, "cmd+g")
    assert isinstance(result, bool)


def test_parse_hotkey_variants():
    from gloss import app

    assert app.parse_hotkey("cmd+g") == (0x05, 0x0100, "⌘G")
    assert app.parse_hotkey("cmd+alt+g") == (0x05, 0x0900, "⌘⌥G")
    assert app.parse_hotkey("ctrl+1")[0] == 0x12
    assert app.parse_hotkey("shift+r") == (0x0F, 0x0200, "⇧R")
    # 无修饰键 / 非法键 / 未知修饰 → None
    assert app.parse_hotkey("g") is None
    assert app.parse_hotkey("cmd+!") is None
    assert app.parse_hotkey("hyper+g") is None
    assert app.parse_hotkey("") is None


def test_config_default_hotkey_is_alt_cmd_g(home):
    """新装默认 ⌥⌘G：⌘+单键会接管其他应用的「查找下一个」（老用户自定义不受影响）。"""
    from gloss.config import load_config

    assert load_config()["app"]["hotkey"] == "cmd+alt+g"


def test_config_auto_lookup_defaults_on_and_parses_bool(home):
    from gloss.config import load_config, set_config_value

    assert load_config()["app"]["auto_lookup"] is True
    set_config_value("app.auto_lookup", "off")
    assert load_config()["app"]["auto_lookup"] is False
    set_config_value("app.auto_lookup", "on")
    assert load_config()["app"]["auto_lookup"] is True


@pytest.fixture
def runner_env(home):
    yield home

"""Default shortcuts are platform-specific, including legacy-default configs."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest
from gloss import config


@pytest.mark.parametrize('platform,expected', [('darwin','cmd+g'), ('win32','ctrl+g')])
@pytest.mark.parametrize('stored', [None, 'cmd+alt+g', 'alt+cmd+g', 'ctrl+shift+k'])
def test_default_shortcut_for_each_platform(home, monkeypatch, platform, expected, stored):
    if stored:
        home.mkdir(parents=True, exist_ok=True)
        (home/'config.toml').write_text('[app]\nhotkey = '+json.dumps(stored)+'\n')
    namespace = {'__name__':'gloss._config_shortcut_test', '__package__':'gloss'}
    monkeypatch.setattr(sys, 'platform', platform)
    exec(compile(Path(config.__file__).read_text(), config.__file__, 'exec'), namespace)
    actual = namespace['load_config']()['app']['hotkey']
    assert actual == ('ctrl+shift+k' if stored == 'ctrl+shift+k' else expected)
    if platform == 'win32' and actual == 'ctrl+g':
        from gloss.hotkey import parse_hotkey
        assert parse_hotkey(actual)[:2] == (0x0002, ord('G'))


def test_legacy_windows_cmd_g_uses_ctrl_g(home, monkeypatch):
    home.mkdir(parents=True, exist_ok=True)
    (home/'config.toml').write_text('[app]\nhotkey = "cmd+g"\n')
    namespace = {'__name__':'gloss._config_shortcut_test', '__package__':'gloss'}
    monkeypatch.setattr(sys, 'platform', 'win32')
    exec(compile(Path(config.__file__).read_text(), config.__file__, 'exec'), namespace)
    assert namespace['load_config']()['app']['hotkey'] == 'ctrl+g'

"""gloss hotkey: spec parsing, serve keep-alive, browser choice, platform guard.

All logic is tested with mocks so the suite runs on every CI platform;
RegisterHotKey itself is only touched by the real Windows process.
"""

from __future__ import annotations

import sys

import pytest

from gloss import hotkey


def test_parse_hotkey_plain_combo():
    assert hotkey.parse_hotkey("ctrl+alt+g") == (0x0002 | 0x0001, ord("G"), "Ctrl+Alt+G")


def test_parse_hotkey_cmd_maps_to_win():
    mods, vk, display = hotkey.parse_hotkey("cmd+g")
    assert (mods, vk) == (0x0008, ord("G"))
    assert display == "Win+G"


def test_parse_hotkey_rejects_bad_specs():
    assert hotkey.parse_hotkey("g") is None  # no modifier
    assert hotkey.parse_hotkey("ctrl+bogus+g") is None
    assert hotkey.parse_hotkey("ctrl+alt+f1") is None  # only a-z / 0-9
    assert hotkey.parse_hotkey("") is None


def test_ensure_serve_skips_spawn_when_port_up(monkeypatch):
    calls = []
    monkeypatch.setattr(hotkey, "_serve_up", lambda port: True)
    monkeypatch.setattr(hotkey.subprocess, "Popen", lambda *a, **k: calls.append(a))
    hotkey._ensure_serve(7789)
    assert calls == []


def test_ensure_serve_spawns_and_waits_when_port_down(monkeypatch):
    spawned = []
    state = {"up": False}
    monkeypatch.setattr(hotkey, "_serve_up", lambda port: state["up"])
    monkeypatch.setattr(hotkey.subprocess, "Popen", lambda *a, **k: spawned.append(a[0]))

    def wait_flips_server_up(_):
        state["up"] = True

    monkeypatch.setattr(hotkey.time, "sleep", wait_flips_server_up)
    hotkey._ensure_serve(7789)
    assert len(spawned) == 1
    cmd = spawned[0]
    assert cmd[1:4] == ["-m", "gloss", "serve"]
    assert "--no-browser" in cmd


def test_open_browser_popup_prefers_chrome_then_edge(monkeypatch):
    spawned = []
    monkeypatch.setattr(hotkey.subprocess, "Popen", lambda cmd, **k: spawned.append(cmd))
    monkeypatch.setattr(hotkey.os.path, "exists", lambda p: p.endswith("chrome.exe"))
    hotkey._open_browser_popup(7789)
    assert spawned[0][0].endswith("chrome.exe")
    assert "--app=http://127.0.0.1:7789" in spawned[0]

    monkeypatch.setattr(hotkey.os.path, "exists", lambda p: False)
    hotkey._open_browser_popup(7789)
    assert spawned[1][0] == "msedge.exe"


def test_open_popup_routes_native_vs_browser(monkeypatch):
    calls = []
    monkeypatch.setattr(hotkey, "_show_native_popup", lambda p: calls.append(("native", p)))
    monkeypatch.setattr(hotkey, "_open_browser_popup", lambda p: calls.append(("browser", p)))

    monkeypatch.setattr(hotkey, "_native_available", lambda: True)
    hotkey._open_popup(7789)
    assert calls == [("native", 7789)]

    monkeypatch.setattr(hotkey, "_native_available", lambda: False)
    hotkey._open_popup(7789)
    assert calls == [("native", 7789), ("browser", 7789)]


@pytest.mark.skipif(sys.platform == "win32", reason="guard only observable off-Windows")
def test_run_hotkey_raises_on_non_windows(monkeypatch):
    from gloss.models import GlossError

    monkeypatch.setattr(hotkey.sys, "platform", "darwin")
    with pytest.raises(GlossError, match="gloss app"):
        hotkey.run_hotkey()


def test_startup_vbs_references_gloss_exe(monkeypatch, tmp_path):
    import shutil

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(shutil, "which", lambda name: r"C:\Users\Admin\.local\bin\gloss.exe")
    hotkey.install_startup()
    content = hotkey.startup_path().read_text(encoding="ascii")
    assert "gloss.exe" in content
    assert " hotkey" in content
    assert hotkey.remove_startup() is True
    assert hotkey.remove_startup() is False

"""Windows 中文控制台（GBK/cp936）回归：CLI 输出必须能被终端编码表示。

历史事故：`gloss ls` 在 chcp 936 的控制台直接 UnicodeEncodeError 崩溃
（rich 渲染 📝 往 GBK 流写）。ui.glyph() 按终端编码能力降级为 ASCII 替身，
这里用 subprocess + PYTHONIOENCODING=gbk 模拟 GBK 控制台，不依赖本地终端。
"""

import os
import subprocess
import sys

import pytest

from gloss import ui

COMMANDS = [["ls"], ["demo"], ["review"], ["stats"], ["--help"]]


def _gbk_env(tmp_path) -> dict:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("PYTHONUTF8", "PYTHONIOENCODING")
    }
    env["PYTHONIOENCODING"] = "gbk"
    env["GLOSS_HOME"] = str(tmp_path)
    return env


@pytest.mark.parametrize("args", COMMANDS)
def test_cli_survives_gbk_console(tmp_path, args):
    proc = subprocess.run(
        [sys.executable, "-m", "gloss", *args],
        env=_gbk_env(tmp_path),
        capture_output=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", errors="replace")
    assert b"Traceback" not in proc.stderr


def test_glyph_falls_back_on_unsupported_encoding(monkeypatch):
    monkeypatch.setattr(ui, "_stream_encoding", lambda: "gbk")
    assert ui.glyph("📖", "[T]") == "[T]"
    assert ui.glyph("📝", "[S]") == "[S]"
    assert ui.glyph("⚡", "*") == "*"
    assert ui.glyph("🌐") == ""  # 默认 ASCII 替身是空串
    # GBK 能表示的字符原样保留（中文正文、间隔号、破折号都不能被误降级）
    assert ui.glyph("词") == "词"
    assert ui.glyph("·", "*") == "·"
    assert ui.glyph("—", "-") == "—"


def test_glyph_keeps_emoji_on_utf8(monkeypatch):
    monkeypatch.setattr(ui, "_stream_encoding", lambda: "utf-8")
    assert ui.glyph("📖", "[T]") == "📖"
    assert ui.glyph("🔥", "TOP") == "🔥"


def test_glyph_choice_is_encodable_in_target_encoding():
    """按目标编码做探测决策时，选出的字符必须真的能编码。"""
    pairs = (("📖", "[T]"), ("📝", "[S]"), ("🔥", "TOP"), ("⚡", "*"),
             ("✨", "+"), ("🔗", "->"), ("🌐", ""), ("✓", "OK"),
             ("✗", "X"), ("⌘", "Cmd+"))
    for enc in ("gbk", "cp1252", "utf-8"):
        for emoji, fallback in pairs:
            chosen = emoji if ui._encodable(emoji, enc) else fallback
            chosen.encode(enc)


def test_type_badge_covers_known_types():
    assert ui._type_badge("term") == ui.glyph("📖", "[T]")
    assert ui._type_badge("snippet") == ui.glyph("📝", "[S]")
    assert ui._type_badge("unknown-type") == ui.glyph("📖", "[T]")

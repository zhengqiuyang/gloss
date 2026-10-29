"""gloss paste: clipboard-driven lookup (text and image paths, mocked clipboard)."""

from __future__ import annotations

import sys

import pytest

from gloss import db
from gloss.cli import cli


def _mock_clipboard(monkeypatch, payload):
    from gloss import clipboard

    monkeypatch.setattr(clipboard, "read_clipboard", lambda: payload)
    # cli imports inside the command body, so patching the module attribute works


def test_paste_short_text_looks_up_term(runner, home, fake_llm, monkeypatch):
    _mock_clipboard(monkeypatch, ("text", "QUIC"))
    result = runner.invoke(cli, ["paste"])
    assert result.exit_code == 0, result.output
    assert fake_llm.term_calls[0]["term"] == "QUIC"
    assert db.get_card("QUIC") is not None


def test_paste_long_text_creates_snippet(runner, home, fake_llm, monkeypatch):
    _mock_clipboard(monkeypatch, ("text", "这是一段很长的阅读内容。" * 20))
    result = runner.invoke(cli, ["paste"])
    assert result.exit_code == 0, result.output
    assert len(fake_llm.snippet_calls) == 1


def test_paste_image_uses_vision_path(runner, home, fake_llm, monkeypatch):
    _mock_clipboard(monkeypatch, ("image", "/tmp/fake-screenshot.png"))
    result = runner.invoke(cli, ["paste"])
    assert result.exit_code == 0, result.output
    assert fake_llm.image_calls == ["/tmp/fake-screenshot.png"]


def test_paste_empty_clipboard_is_friendly_error(runner, home, fake_llm, monkeypatch):
    from gloss.models import GlossError

    _mock_clipboard(monkeypatch, ("text", "  "))
    # read_clipboard raises GlossError on empty; simulate by raising variant
    from gloss import clipboard

    def raise_empty():
        raise GlossError("剪贴板里没有文字也没有图片。")

    monkeypatch.setattr(clipboard, "read_clipboard", raise_empty)
    result = runner.invoke(cli, ["paste"])
    assert result.exit_code != 0
    assert "剪贴板" in str(result.exception)


def test_paste_image_without_vision_model_prints_hint(runner, home, fake_llm, monkeypatch):
    _mock_clipboard(monkeypatch, ("image", "/tmp/fake-screenshot.png"))
    result = runner.invoke(cli, ["paste"])
    # vision_model is unset by default; the hint should show but ingest still runs
    assert "视觉模型" in result.output


# ---------------------------------------------------------------------------
# platform dispatch in read_clipboard (backends monkeypatched, runs everywhere)
# ---------------------------------------------------------------------------

def test_read_clipboard_text_wins_over_image(monkeypatch):
    from gloss import clipboard

    monkeypatch.setattr(clipboard, "_clipboard_text", lambda: "  QUIC  ")
    monkeypatch.setattr(clipboard, "_clipboard_png", lambda: "/tmp/x.png")
    assert clipboard.read_clipboard() == ("text", "QUIC")


def test_read_clipboard_falls_back_to_image(monkeypatch):
    from gloss import clipboard

    monkeypatch.setattr(clipboard, "_clipboard_text", lambda: "   ")
    monkeypatch.setattr(clipboard, "_clipboard_png", lambda: "/tmp/x.png")
    assert clipboard.read_clipboard() == ("image", "/tmp/x.png")


def test_read_clipboard_empty_raises_with_platform_note(monkeypatch):
    from gloss import clipboard
    from gloss.models import GlossError

    monkeypatch.setattr(clipboard, "_clipboard_text", lambda: None)
    monkeypatch.setattr(clipboard, "_clipboard_png", lambda: None)
    with pytest.raises(GlossError):
        clipboard.read_clipboard()


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 clipboard")
def test_win32_text_smoke():
    from gloss.clipboard import _win32_text

    value = _win32_text()
    assert value is None or isinstance(value, str)

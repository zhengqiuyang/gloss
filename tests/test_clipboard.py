"""gloss paste: clipboard-driven lookup (text and image paths, mocked clipboard)."""

from __future__ import annotations

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

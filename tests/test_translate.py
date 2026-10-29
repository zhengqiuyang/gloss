"""Translation: cache economics, CLI command, and the /api/translate endpoint."""

from __future__ import annotations

import json

from gloss import db, ingest
from gloss.cli import cli

from conftest import http_request as _request


def test_translate_calls_llm_and_caches(home, fake_llm):
    r1 = ingest.translate_text("hello world")
    assert r1["cache_hit"] is False
    assert r1["translation"] == "你好，世界"
    assert fake_llm.translate_calls == [("hello world", "中文")]

    r2 = ingest.translate_text("hello world")
    assert r2["cache_hit"] is True
    assert r2["translation"] == "你好，世界"
    assert r2["hits"] == 1
    assert len(fake_llm.translate_calls) == 1  # 第二次零 token


def test_translate_cache_is_keyed_by_text_and_target(home, fake_llm):
    ingest.translate_text("hello world")
    fake_llm.translate_queue.append(("另一个译文", 60))
    ingest.translate_text("different text")
    assert len(fake_llm.translate_calls) == 2  # 不同文本不共享缓存


def test_translate_rejects_empty(home, fake_llm):
    import pytest
    from gloss.models import GlossError

    with pytest.raises(GlossError):
        ingest.translate_text("   ")


def test_tr_command_reads_clipboard(runner, home, fake_llm, monkeypatch):
    from gloss import clipboard

    monkeypatch.setattr(clipboard, "read_clipboard", lambda: ("text", "hello world"))
    result = runner.invoke(cli, ["tr"])
    assert result.exit_code == 0, result.output
    assert "你好，世界" in result.output


def test_tr_command_with_argument(runner, home, fake_llm):
    result = runner.invoke(cli, ["tr", "stay hungry"])
    assert result.exit_code == 0, result.output
    assert fake_llm.translate_calls[0][0] == "stay hungry"


def test_api_translate_endpoint(server_url, fake_llm):
    status, _, data = _request(server_url, "POST", {"text": "hello"}, path="/api/translate")
    assert status == 200
    payload = json.loads(data)
    assert payload["cache_hit"] is False
    assert payload["translation"] == "你好，世界"

    status, _, data = _request(server_url, "POST", {"text": "hello"}, path="/api/translate")
    assert json.loads(data)["cache_hit"] is True


def test_page_has_translate_button(server_url):
    status, _, data = _request(server_url)
    assert status == 200
    html = data.decode("utf-8")
    assert "trbtn" in html
    assert "/api/translate" in html

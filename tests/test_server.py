"""L2 local server: API endpoints and the popup page."""

from __future__ import annotations

import http.client
import json

from gloss import db

from conftest import http_request as _request


def test_get_page_contains_input_and_prefill(server_url, monkeypatch):
    from gloss import clipboard

    monkeypatch.setattr(clipboard, "read_clipboard", lambda: ("text", "QUIC"))
    status, ctype, data = _request(server_url)
    assert status == 200
    assert "text/html" in ctype
    html = data.decode("utf-8")
    assert "<input" in html
    assert "QUIC" in html  # clipboard pre-filled


def test_glass_param_toggles_body_class(server_url):
    _, _, plain = _request(server_url)
    assert "<body>" in plain.decode("utf-8")

    conn = http.client.HTTPConnection(server_url, timeout=30)
    conn.request("GET", "/?glass=1")
    resp = conn.getresponse()
    glass_html = resp.read().decode("utf-8")
    conn.close()
    assert resp.status == 200  # 带查询参数也必须路由到页面
    assert '<body class="glass">' in glass_html


def test_get_page_with_image_clipboard_shows_hint(server_url, monkeypatch):
    from gloss import clipboard

    monkeypatch.setattr(clipboard, "read_clipboard", lambda: ("image", "/tmp/x.png"))
    _, _, data = _request(server_url)
    assert "图片" in data.decode("utf-8")


def test_lookup_new_term_returns_card(server_url):
    status, _, data = _request(server_url, "POST", {"text": "RAG"})
    assert status == 200
    payload = json.loads(data)
    assert payload["cache_hit"] is False
    assert payload["card"]["term"] == "RAG"
    assert payload["card"]["one_liner"]
    assert db.get_card("RAG") is not None


def test_lookup_again_is_cache_hit(server_url):
    _request(server_url, "POST", {"text": "RAG"})
    status, _, data = _request(server_url, "POST", {"text": "rag"})
    payload = json.loads(data)
    assert status == 200
    assert payload["cache_hit"] is True


def test_lookup_includes_linked_and_pending(server_url):
    from conftest import make_card

    make_card("Embedding", one_liner="向量化")
    status, _, data = _request(server_url, "POST", {"text": "RAG"})
    payload = json.loads(data)
    linked_terms = [x["term"] for x in payload["card"]["linked"]]
    assert "Embedding" in linked_terms
    assert "向量数据库" in payload["card"]["pending"]


def test_long_text_returns_snippet_with_key_terms(server_url):
    status, _, data = _request(server_url, "POST", {"text": "这是一段足够长的阅读内容。" * 20})
    payload = json.loads(data)
    assert status == 200
    assert payload["card"]["type"] == "snippet"
    assert payload["key_terms"]


def test_empty_text_is_400(server_url):
    status, _, data = _request(server_url, "POST", {"text": "  "})
    assert status == 400
    assert "error" in json.loads(data)


def test_bad_json_is_400(server_url):
    conn = http.client.HTTPConnection(server_url, timeout=30)
    conn.request("POST", "/api/lookup", body=b"not json", headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    assert resp.status == 400
    conn.close()


def test_unknown_path_is_404(server_url):
    status, _, _ = _request(server_url)
    conn = http.client.HTTPConnection(server_url, timeout=30)
    conn.request("GET", "/nope")
    assert conn.getresponse().status == 404
    conn.close()


def test_page_contains_auto_trigger_logic(server_url, monkeypatch):
    from gloss import clipboard

    monkeypatch.setattr(clipboard, "read_clipboard", lambda: ("text", "QUIC"))
    _, _, data = _request(server_url)
    html = data.decode("utf-8")
    assert "/api/image" in html
    assert "CLIP_KIND" in html
    assert 'PREFILL.trim()) lookup()' in html  # 打开即自动查询


def test_api_image_with_clipboard_image(server_url, fake_llm, monkeypatch):
    from gloss import clipboard

    monkeypatch.setattr(clipboard, "read_clipboard", lambda: ("image", "/tmp/shot.png"))
    status, _, data = _request(server_url, "POST", {"text": "ignored"}, path="/api/image")
    assert status == 200
    payload = json.loads(data)
    assert payload["card"]["type"] == "snippet"
    assert fake_llm.image_calls == ["/tmp/shot.png"]


def test_api_image_without_image_is_400(server_url, fake_llm, monkeypatch):
    from gloss import clipboard

    monkeypatch.setattr(clipboard, "read_clipboard", lambda: ("text", "just a word"))
    status, _, data = _request(server_url, "POST", {}, path="/api/image")
    assert status == 400
    assert "没有图片" in json.loads(data)["error"]

"""L2 local server: API endpoints and the popup page."""

from __future__ import annotations

import http.client
import json
import threading
from typing import Optional

import pytest

from gloss import db
from gloss.server import start_server


@pytest.fixture()
def server_url(home, fake_llm):
    srv = start_server(0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _request(url, method="GET", payload: Optional[dict] = None):
    conn = http.client.HTTPConnection(url, timeout=30)
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    conn.request(method, "/" if payload is None else "/api/lookup", body=body, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    ctype = resp.getheader("Content-Type", "")
    conn.close()
    return resp.status, ctype, data


def test_get_page_contains_input_and_prefill(server_url, monkeypatch):
    from gloss import clipboard

    monkeypatch.setattr(clipboard, "read_clipboard", lambda: ("text", "QUIC"))
    status, ctype, data = _request(server_url)
    assert status == 200
    assert "text/html" in ctype
    html = data.decode("utf-8")
    assert "<input" in html
    assert "QUIC" in html  # clipboard pre-filled


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

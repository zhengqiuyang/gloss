"""JSON extraction robustness and prompt construction."""

from __future__ import annotations

import pytest

from gloss.llm import GlossLLM, extract_json
from gloss.models import GlossError


def test_extract_json_plain():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_with_code_fence():
    text = '```json\n{"term": "RAG", "n": 2}\n```'
    assert extract_json(text) == {"term": "RAG", "n": 2}


def test_extract_json_with_leading_prose():
    text = 'Here is your card:\n{"a": {"b": "}"}, "c": []}\nHope it helps!'
    assert extract_json(text) == {"a": {"b": "}"}, "c": []}


def test_extract_json_braces_inside_strings():
    assert extract_json('{"detail": "用 {curly} braces", "x": "}"}') == {
        "detail": "用 {curly} braces",
        "x": "}",
    }


def test_extract_json_rejects_non_json():
    with pytest.raises(GlossError):
        extract_json("no json here at all")


def test_extract_json_rejects_truncated():
    with pytest.raises(GlossError):
        extract_json('{"a": 1, "b":')


def test_extract_json_rejects_non_object():
    with pytest.raises(GlossError):
        extract_json('[1, 2, 3]')


def _llm(**overrides):
    cfg = {
        "llm": {"base_url": "http://x/v1", "model": "m", "vision_model": "", "timeout": 1},
        "glossary": {
            "language": "中文",
            "detail_max_chars": 160,
            "max_known_terms": 200,
        },
        **overrides,
    }
    return GlossLLM(cfg)


def test_term_prompt_contains_known_terms_and_language(monkeypatch):
    captured = {}

    def fake_chat_json(self, system, user, image_path=None):
        captured["system"], captured["user"] = system, user
        return {"term": "RAG", "aliases": [], "one_liner": "", "detail": "", "tags": [], "related": []}, 0

    monkeypatch.setattr(GlossLLM, "chat_json", fake_chat_json)
    data, _ = _llm().explain_term(
        "RAG",
        known_terms=["Embedding"],
        context="在读文档",
        source="https://x",
    )
    assert data["term"] == "RAG"
    assert "Embedding" in captured["user"]
    assert "在读文档" in captured["user"]
    assert "https://x" in captured["user"]
    assert "中文" in captured["system"]
    assert "known_terms" in captured["user"]


def test_digest_prompt_includes_text(monkeypatch):
    captured = {}

    def fake_chat_json(self, system, user, image_path=None):
        captured["user"] = user
        return {"title": "t", "one_liner": "", "detail": "", "tags": [], "key_terms": []}, 0

    monkeypatch.setattr(GlossLLM, "chat_json", fake_chat_json)
    _llm().digest_text("AI agents need memory", known_terms=[])
    assert "AI agents need memory" in captured["user"]


def test_extract_json_from_typical_llm_reply():
    reply = (
        "好的，以下是词条：\n\n"
        '{"term": "QUIC", "aliases": ["HTTP/3 transport"], '
        '"one_liner": "基于 UDP 的低延迟传输协议", '
        '"detail": "QUIC 由 Google 提出……包含 {TLS 1.3} 握手。", '
        '"tags": ["network"], "related": ["TCP", "HTTP/3"]}\n'
    )
    data = extract_json(reply)
    assert data["term"] == "QUIC"
    assert data["related"] == ["TCP", "HTTP/3"]

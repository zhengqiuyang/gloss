"""Shared fixtures: isolated GLOSS_HOME and a fake LLM client (no network)."""

from __future__ import annotations

import http.client
import json
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from gloss import db  # noqa: E402
from gloss.models import Card, GlossError  # noqa: E402


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Point GLOSS_HOME at a throwaway dir and reset the db connection cache."""
    root = tmp_path / "gloss-home"
    monkeypatch.setenv("GLOSS_HOME", str(root))
    db.close()
    yield root
    db.close()


TERM_PAYLOAD = {
    "term": "RAG",
    "aliases": ["Retrieval-Augmented Generation", "检索增强生成"],
    "one_liner": "让模型先检索资料再作答的技术。",
    "detail": "先从外部知识库检索相关片段，再拼进上下文交给大模型生成答案，能显著减少幻觉。",
    "tags": ["machine-learning", "nlp"],
    "related": ["Embedding", "向量数据库", "langchain"],
}

SNIPPET_PAYLOAD = {
    "title": "关于 agent 记忆设计的笔记",
    "one_liner": "agent 的长期记忆应该分层：工作记忆放上下文，长期记忆放外部存储。",
    "detail": "笔记讨论了记忆分层、检索时机和遗忘策略，强调记忆写入需要显式触发以避免噪声。",
    "tags": ["ai-agents"],
    "key_terms": ["RAG", "MemGPT", "向量数据库"],
}


class FakeLLM:
    """Records prompts and returns queued payloads, mimicking gloss.llm.GlossLLM."""

    def __init__(self):
        self.term_queue: List[Tuple[Dict[str, Any], int]] = [(dict(TERM_PAYLOAD), 900)]
        self.snippet_queue: List[Tuple[Dict[str, Any], int]] = [(dict(SNIPPET_PAYLOAD), 1200)]
        self.image_queue: List[Tuple[Dict[str, Any], int]] = [(dict(SNIPPET_PAYLOAD), 1500)]
        self.translate_queue: List[Tuple[str, int]] = [("你好，世界", 60)]
        self.term_calls: List[Dict[str, Any]] = []
        self.snippet_calls: List[str] = []
        self.image_calls: List[str] = []
        self.translate_calls: List[Tuple[str, str]] = []

    def explain_term(self, term, known_terms, context="", source=""):
        self.term_calls.append(
            {"term": term, "known_terms": list(known_terms), "context": context, "source": source}
        )
        if not self.term_queue:
            raise GlossError("FakeLLM 队列为空")
        return self.term_queue.pop(0)

    def digest_text(self, text, known_terms):
        self.snippet_calls.append(text)
        return self.snippet_queue.pop(0)

    def analyze_image(self, image_path, note, known_terms):
        self.image_calls.append(image_path)
        return self.image_queue.pop(0)

    def translate(self, text, target):
        self.translate_calls.append((text, target))
        if not self.translate_queue:
            raise GlossError("FakeLLM 队列为空")
        return self.translate_queue.pop(0)


@pytest.fixture()
def fake_llm(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr("gloss.llm.get_client", lambda: fake)
    yield fake


@pytest.fixture()
def runner():
    return CliRunner()


def make_card(term, **kwargs) -> Card:
    card = Card(term=term, **kwargs)
    db.insert_card(card)
    return card


# -- shared server helpers (used by test_server and test_translate) ----------

@pytest.fixture()
def server_url(home, fake_llm):
    from gloss.server import start_server

    srv = start_server(0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def http_request(url, method="GET", payload: Optional[dict] = None, path: Optional[str] = None):
    conn = http.client.HTTPConnection(url, timeout=30)
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    if path is None:
        path = "/" if payload is None else "/api/lookup"
    conn.request(method, path, body=body, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    ctype = resp.getheader("Content-Type", "")
    conn.close()
    return resp.status, ctype, data

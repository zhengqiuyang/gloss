"""Shared fixtures: isolated GLOSS_HOME and a fake LLM client (no network)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

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
        self.term_calls: List[Dict[str, Any]] = []
        self.snippet_calls: List[str] = []
        self.image_calls: List[str] = []

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

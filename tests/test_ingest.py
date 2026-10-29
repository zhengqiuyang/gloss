"""Ingest flow: query-as-cache, auto-linking, refresh, digest routing."""

from __future__ import annotations

import pytest

from gloss import db, ingest
from gloss.models import GlossError

from conftest import TERM_PAYLOAD, make_card


def test_first_lookup_ingests_via_llm(home, fake_llm):
    ingest.lookup_or_ingest("RAG")
    assert len(fake_llm.term_calls) == 1
    call = fake_llm.term_calls[0]
    assert call["term"] == "RAG"
    card = db.get_card("RAG")
    assert card.one_liner == "让模型先检索资料再作答的技术。"
    assert card.tokens_used == 900
    assert card.tags == ["machine-learning", "nlp"]


def test_second_lookup_is_a_cache_hit_and_skips_llm(home, fake_llm):
    ingest.lookup_or_ingest("RAG")
    ingest.lookup_or_ingest("rag")  # case-insensitive
    assert len(fake_llm.term_calls) == 1
    assert db.get_card("RAG").hit_count == 1


def test_alias_lookup_is_a_cache_hit(home, fake_llm):
    ingest.lookup_or_ingest("RAG")  # payload includes alias 检索增强生成
    ingest.lookup_or_ingest("检索增强生成")
    assert len(fake_llm.term_calls) == 1
    assert db.get_card("RAG").hit_count == 1


def test_related_names_link_to_existing_cards(home, fake_llm):
    known = make_card("Embedding", one_liner="把文本变成向量")
    ingest.lookup_or_ingest("RAG")  # payload related: Embedding, 向量数据库, langchain
    assert db.has_link(db.get_card("RAG").id, known.id)
    card = db.get_card("RAG")
    assert "向量数据库" in card.related_names  # pending, not lost
    assert "langchain" in card.related_names


def test_known_terms_are_passed_to_prompt(home, fake_llm):
    make_card("Embedding")
    make_card("HNSW")
    ingest.lookup_or_ingest("RAG")
    known = fake_llm.term_calls[0]["known_terms"]
    assert "Embedding" in known and "HNSW" in known


def test_backfill_on_later_ingest(home, fake_llm):
    ingest.lookup_or_ingest("RAG")  # parks 向量数据库 / langchain as pending
    fake_llm.term_queue.append(
        (
            {
                "term": "向量数据库",
                "aliases": ["vector database"],
                "one_liner": "存向量的库",
                "detail": "支持相似度检索。",
                "tags": ["machine-learning"],
                "related": ["RAG"],
            },
            800,
        )
    )
    ingest.lookup_or_ingest("向量数据库")

    rag = db.get_card("RAG")
    vector_db = db.get_card("向量数据库")
    assert vector_db is not None
    assert db.has_link(rag.id, vector_db.id)
    assert "向量数据库" not in rag.related_names  # backfilled off the pending list


def test_llm_name_collision_keeps_user_spelling(home, fake_llm):
    ingest.lookup_or_ingest("RAG")
    fake_llm.term_queue.append((dict(TERM_PAYLOAD), 900))  # same payload term: RAG
    ingest.lookup_or_ingest("向量数据库")  # payload renames to existing "RAG"
    card = db.get_card("向量数据库")
    assert card.term == "向量数据库"
    assert any("RAG" == a for a in card.aliases)


def test_refresh_replaces_content_but_keeps_history(home, fake_llm):
    ingest.lookup_or_ingest("RAG")
    db.increment_hit(db.get_card("RAG").id)
    fake_llm.term_queue.append(
        (
            {
                "term": "RAG",
                "aliases": [],
                "one_liner": "更新后的一句话",
                "detail": "更新后的详情",
                "tags": ["nlp"],
                "related": [],
            },
            700,
        )
    )
    ingest.lookup_or_ingest("RAG", refresh=True)
    card = db.get_card("RAG")
    assert card.one_liner == "更新后的一句话"
    assert card.hit_count == 1  # history kept
    assert card.tokens_used == 900 + 700
    assert len(fake_llm.term_calls) == 2


def test_note_and_source_recorded(home, fake_llm):
    ingest.lookup_or_ingest("RAG", note="在读 agent 文档", source="https://example.com")
    call = fake_llm.term_calls[0]
    assert call["context"] == "在读 agent 文档"
    card = db.get_card("RAG")
    assert card.context == "在读 agent 文档"
    assert card.source == "https://example.com"


def test_llm_normalizes_name_keeps_user_spelling_as_alias(home, fake_llm):
    payload = dict(
        term="retrieval-augmented generation (RAG)",
        aliases=["RAG"],
        one_liner="x",
        detail="y",
        tags=["nlp"],
        related=[],
    )
    fake_llm.term_queue = [(payload, 100)]
    ingest.lookup_or_ingest("rag")
    card = db.get_card("rag")
    assert card.term == "retrieval-augmented generation (RAG)"
    assert "rag" in [a.lower() for a in card.aliases]


def test_empty_term_raises(home, fake_llm):
    with pytest.raises(GlossError):
        ingest.lookup_or_ingest("   ")


def test_piped_short_text_routes_to_term_lookup(home, fake_llm):
    ingest.ingest_piped("QUIC")
    assert fake_llm.term_calls[0]["term"] == "QUIC"
    assert db.get_card("QUIC") is not None


def test_piped_long_text_creates_snippet_with_key_terms(home, fake_llm):
    long_text = "这是一段很长的文字。" * 30
    ingest.ingest_piped(long_text)
    assert len(fake_llm.snippet_calls) == 1
    assert db.get_card("关于 agent 记忆设计的笔记").type == "snippet"
    assert len(fake_llm.term_calls) == 0  # key terms are suggestions, not auto-ingested


def test_snippet_title_collision_gets_suffix(home, fake_llm):
    ingest.ingest_piped("一段内容 " * 20)
    fake_llm.snippet_queue.append((dict(title="关于 agent 记忆设计的笔记", one_liner="b", detail="c", tags=[], key_terms=[]), 10))
    ingest.ingest_piped("另一段内容 " * 20)
    assert db.get_card("关于 agent 记忆设计的笔记 (2)") is not None


def test_image_ingest_creates_snippet(home, fake_llm, tmp_path):
    png = tmp_path / "shot.png"
    png.write_bytes(b"\x89PNG fake image bytes")
    ingest.ingest_image(str(png))
    assert fake_llm.image_calls == [str(png)]
    assert db.get_card("关于 agent 记忆设计的笔记").type == "snippet"

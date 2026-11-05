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
    assert "向量数据库" in rag.related_names  # 提及保留
    assert db.pending_names(rag) == ["Embedding", "langchain"]  # 只剩真正未收录的


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


# -- 入库校验：JSON 能解析不等于卡片有效 -------------------------------------


def test_empty_payload_is_rejected_not_saved(home, fake_llm):
    fake_llm.term_queue = [({}, 10)]
    with pytest.raises(GlossError):
        ingest.lookup_or_ingest("JUNK")
    assert db.get_card("JUNK") is None
    assert db.counts()["total"] == 0


def test_payload_missing_detail_is_rejected(home, fake_llm):
    fake_llm.term_queue = [({"term": "X", "one_liner": "只有一句话"}, 10)]
    with pytest.raises(GlossError):
        ingest.lookup_or_ingest("X")
    assert db.get_card("X") is None


def test_non_dict_payload_is_rejected(home, fake_llm):
    fake_llm.term_queue = [("一段字符串", 10)]
    with pytest.raises(GlossError):
        ingest.lookup_or_ingest("X")
    assert db.get_card("X") is None


def test_non_string_core_fields_are_rejected(home, fake_llm):
    """str() 强转会让字典/列表混进卡片正文——必须先验类型。"""
    fake_llm.term_queue = [({"term": "X", "one_liner": {"text": "y"}, "detail": "z"}, 10)]
    with pytest.raises(GlossError):
        ingest.lookup_or_ingest("X")
    assert db.get_card("X") is None


def test_numeric_term_is_rejected(home, fake_llm):
    fake_llm.term_queue = [({"term": 123, "one_liner": "a", "detail": "b"}, 10)]
    with pytest.raises(GlossError):
        ingest.lookup_or_ingest("X")
    assert db.get_card("X") is None


def test_snippet_payload_without_title_is_rejected(home, fake_llm):
    fake_llm.snippet_queue = [({"one_liner": "y", "detail": "z"}, 10)]
    with pytest.raises(GlossError):
        ingest.ingest_piped("一段足够长的内容 " * 10)
    assert db.counts()["total"] == 0


# -- 长文本：明确拒绝，而不是静默截断 ----------------------------------------


def test_oversize_paste_is_refused_before_llm(home, fake_llm):
    with pytest.raises(GlossError) as exc:
        ingest.ingest_piped("长" * 8001)
    assert "8000" in str(exc.value)
    assert fake_llm.snippet_calls == []  # 根本没把截断后的内容发给模型


def test_oversize_translate_is_refused(home, fake_llm):
    with pytest.raises(GlossError) as exc:
        ingest.translate_text("长" * 8001)
    assert "8000" in str(exc.value)
    assert fake_llm.translate_calls == []


# -- 别名补链与关联替换 ------------------------------------------------------


def test_adding_alias_backfills_pending(home, fake_llm):
    note = make_card("阅读笔记", related_names=["Vector DB"])
    db.resolve_related(note)
    rag = make_card("RAG", one_liner="x")
    ingest.patch_card(rag, aliases=["vector db"], additive=True)  # 新增别名匹配 pending（大小写不敏感）
    assert db.has_link(note.id, rag.id)
    assert db.pending_names(db.get_card("阅读笔记")) == []


def test_replace_related_prunes_stale_links(home, fake_llm):
    a = make_card("RAG", one_liner="x")
    b = make_card("Embedding", one_liner="x")
    c = make_card("Faiss", one_liner="x")
    ingest.patch_card(a, related=["Embedding"])  # 替换语义：related 变成 [Embedding]
    assert db.has_link(a.id, b.id)
    ingest.patch_card(a, related=["Faiss"])  # 再替换：旧链接要被清掉
    assert not db.has_link(a.id, b.id)
    assert db.has_link(a.id, c.id)


def test_replace_related_keeps_link_the_other_side_mentions(home, fake_llm):
    a = make_card("RAG", one_liner="x")
    b = make_card("Embedding", one_liner="x", related_names=["RAG"])
    db.resolve_related(b)
    ingest.patch_card(a, related=["Faiss"])  # a 不再提 Embedding，但对方还提着 RAG
    assert db.has_link(a.id, b.id)


def test_refresh_prunes_links_of_cleared_related(home, fake_llm):
    make_card("Embedding", one_liner="x")
    ingest.lookup_or_ingest("RAG")  # payload related 含 Embedding → 建链
    assert db.has_link(db.get_card("RAG").id, db.get_card("Embedding").id)
    fake_llm.term_queue.append(
        (dict(TERM_PAYLOAD, related=[], aliases=[], tags=["nlp"]), 100)
    )
    ingest.lookup_or_ingest("RAG", refresh=True)
    assert not db.has_link(db.get_card("RAG").id, db.get_card("Embedding").id)

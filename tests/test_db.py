"""Storage layer: cards, aliases, FTS search, links, backfill."""

from __future__ import annotations

from gloss import db
from gloss.models import Card, norm_term

from conftest import make_card


def test_lookup_by_term_is_case_insensitive(home):
    make_card("RAG", one_liner="x")
    assert db.get_card("rag").term == "RAG"
    assert db.get_card("  RAG  ").id == db.get_card("RAG").id


def test_lookup_by_alias(home):
    card = make_card("RAG", aliases=["Retrieval-Augmented Generation", "检索增强生成"])
    assert db.get_card("retrieval-augmented generation").id == card.id
    assert db.get_card("检索增强生成").id == card.id


def test_fts_search_matches_detail(home):
    make_card("RAG", one_liner="先检索再生成", detail="减少幻觉，用于知识库问答")
    make_card("LSP", one_liner="语言服务器协议")
    hits = db.search("幻觉")
    assert [c.term for c in hits] == ["RAG"]


def test_search_cjk_falls_back_to_like(home):
    make_card("向量数据库", one_liner="存 embedding 的库")
    hits = db.search("向量")
    assert any(c.term == "向量数据库" for c in hits)


def test_search_unknown_query_is_empty(home):
    make_card("RAG")
    assert db.search("zzzqqq") == []


def test_links_are_undirected_and_deduped(home):
    a = make_card("RAG")
    b = make_card("Embedding")
    assert db.add_link(a.id, b.id) is True
    assert db.add_link(b.id, a.id) is False  # same pair, reversed
    assert [c.term for c in db.neighbors(a.id)] == ["Embedding"]
    assert [c.term for c in db.neighbors(b.id)] == ["RAG"]
    assert not db.neighbors(make_card("LSP").id)


def test_resolve_related_links_known_and_parks_unknown(home):
    known = make_card("Embedding")
    card = make_card("RAG", related_names=["Embedding", "Faiss"])
    linked, pending = db.resolve_related(card)
    assert [c.term for c in linked] == ["Embedding"]
    assert pending == ["Faiss"]
    assert db.has_link(card.id, known.id)
    assert db.get_card("RAG").related_names == ["Faiss"]


def test_backfill_links_later_card_into_pending(home):
    rag = make_card("RAG", related_names=["Faiss"])
    db.resolve_related(rag)
    faiss = make_card("Faiss", aliases=["faiss"])

    newly = db.backfill_links(faiss.id, faiss.norm)

    assert [c.term for c in newly] == ["RAG"]
    assert db.has_link(rag.id, faiss.id)
    assert db.get_card("RAG").related_names == []


def test_backfill_is_case_insensitive(home):
    rag = make_card("RAG", related_names=["HNSW"])
    db.resolve_related(rag)
    hnsw = make_card("hnsw")
    newly = db.backfill_links(hnsw.id, hnsw.norm)
    assert newly and db.has_link(rag.id, hnsw.id)


def test_hit_counter(home):
    card = make_card("RAG")
    db.increment_hit(card.id)
    db.increment_hit(card.id)
    assert db.get_card("RAG").hit_count == 2


def test_counts_and_top_hits(home):
    a = make_card("RAG", tokens_used=900)
    b = make_card("Embedding")
    db.increment_hit(a.id)
    stats = db.counts()
    assert stats["total"] == 2
    assert stats["by_type"]["term"] == 2
    assert stats["cache_hits"] == 1
    assert stats["tokens_spent"] == 900
    assert [c.term for c in db.top_hits()] == ["RAG"]


def test_list_cards_filter_by_tag(home):
    make_card("RAG", tags=["nlp"])
    make_card("Flexbox", tags=["css"])
    assert [c.term for c in db.list_cards(tag="css")] == ["Flexbox"]


def test_update_card_rewrites_aliases(home):
    card = make_card("RAG", aliases=["old-alias"])
    card.aliases = ["new-alias"]
    db.update_card(card)
    assert db.get_card("old-alias") is None
    assert db.get_card("new-alias").id == card.id


def test_norm_term_normalizes_whitespace_and_case():
    assert norm_term("  Retrieval   AUGMENTED ") == "retrieval augmented"

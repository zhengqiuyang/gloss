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
    # 提及列表保持完整：已解析的也在，"待收录"是派生值
    assert db.get_card("RAG").related_names == ["Embedding", "Faiss"]
    assert db.pending_names(db.get_card("RAG")) == ["Faiss"]


def test_backfill_links_later_card_into_pending(home):
    rag = make_card("RAG", related_names=["Faiss"])
    db.resolve_related(rag)
    faiss = make_card("Faiss", aliases=["faiss"])

    newly = db.backfill_links(faiss)

    assert [c.term for c in newly] == ["RAG"]
    assert db.has_link(rag.id, faiss.id)
    assert db.pending_names(db.get_card("RAG")) == []  # 提及保留，但已解析


def test_backfill_is_case_insensitive(home):
    rag = make_card("RAG", related_names=["HNSW"])
    db.resolve_related(rag)
    hnsw = make_card("hnsw")
    newly = db.backfill_links(hnsw)
    assert newly and db.has_link(rag.id, hnsw.id)


def test_backfill_matches_pending_alias_of_later_card(home):
    """先挂起「检索增强生成」，后创建别名含它的英文卡片 → 自动补链。"""
    note = make_card("阅读笔记", related_names=["检索增强生成"])
    db.resolve_related(note)
    rag = make_card("RAG", aliases=["Retrieval-Augmented Generation", "检索增强生成"])

    newly = db.backfill_links(rag)

    assert [c.term for c in newly] == ["阅读笔记"]
    assert db.has_link(note.id, rag.id)
    assert db.pending_names(db.get_card("阅读笔记")) == []  # 提及保留，但已解析


def test_prune_stale_links_drops_links_neither_side_mentions(home):
    a = make_card("RAG")
    b = make_card("Embedding")
    c = make_card("Faiss")
    db.add_link(a.id, b.id)
    db.add_link(a.id, c.id)
    removed = db.prune_stale_links(a, {b.id, c.id})  # a 现在谁都不提
    assert sorted(removed) == sorted([b.id, c.id])
    assert not db.has_link(a.id, b.id)
    assert not db.has_link(a.id, c.id)


def test_prune_stale_links_kept_when_other_side_mentions(home):
    a = make_card("RAG")
    b = make_card("Embedding", related_names=["RAG"])
    db.add_link(a.id, b.id)
    assert db.prune_stale_links(a, {b.id}) == []  # 对方还提着 RAG，链接保留
    assert db.has_link(a.id, b.id)


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

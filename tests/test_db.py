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


def test_backfill_skips_conflicting_alias_holder(home):
    """P1 回归：别名 norm 被 X 占用后，Y 重复声明同一别名时，查询仍解析到 X，
    补链也必须只认解析结果——Y 不得通过补链抢走 X 的关联。"""
    w = make_card("W卡片", related_names=["shared"])
    db.resolve_related(w)  # shared 还没有归属 → 挂起
    x = make_card("X卡片", aliases=["shared"])
    newly_x = db.backfill_links(x)
    assert [c.term for c in newly_x] == ["W卡片"]  # 真正的归属者补链成功
    assert db.has_link(w.id, x.id)

    y = make_card("Y卡片", aliases=["shared"])  # 重复声明（alias_index 仍指向 X）
    assert db.get_card("shared").id == x.id
    newly_y = db.backfill_links(y)
    assert newly_y == []
    assert not db.has_link(w.id, y.id)


def test_upgrade_from_consumed_mentions_keeps_links(home):
    """P2 回归：旧版会把已解析的 related_names 消费掉，链接脱离声明存在。
    迁移必须把声明补回来，替换/刷新的清理才不会误删旧关系。"""
    rag = make_card("RAG")
    emb = make_card("Embedding")
    db.add_link(rag.id, emb.id)
    # 模拟旧版存储形态：链接存在，但双方的提及声明都已被旧版本消费
    conn = db.connect()
    conn.execute("DELETE FROM meta WHERE key = 'mentions_model'")
    conn.execute(
        "UPDATE cards SET related_names = '[]' WHERE id IN (?, ?)", (rag.id, emb.id)
    )
    conn.commit()
    db.close()  # 断开缓存连接 → 下次访问触发迁移

    assert db.get_card("RAG").related_names == ["Embedding"]
    assert db.get_card("Embedding").related_names == ["RAG"]
    rag2 = db.get_card("RAG")
    old = {c.id for c in db.neighbors(rag2.id)}
    db.prune_stale_links(rag2, old)  # 替换/刷新场景的清理
    assert db.has_link(rag.id, emb.id)  # 旧关系因声明恢复而保住


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


def test_upgrade_preserves_one_way_declaration_then_allows_removal(home):
    from gloss.ingest import patch_card

    a = make_card('A', related_names=['B'])
    b = make_card('B')
    db.resolve_related(a)
    db.connect().execute("DELETE FROM meta WHERE key = 'mentions_model'")
    db.connect().commit()
    db.close()

    a = db.get_card('A')
    b = db.get_card('B')
    assert a.related_names == ['B']
    assert b.related_names == []  # Migration must not invent B -> A.
    patch_card(a, related=[])
    assert not db.has_link(a.id, b.id)


def test_upgrade_keeps_alias_based_direction_and_other_pending_names(home):
    from gloss.ingest import patch_card

    a = make_card('A', aliases=['alias A'])
    b = make_card('B', related_names=['ALIAS A', 'not yet collected'])
    db.resolve_related(b)
    db.connect().execute("DELETE FROM meta WHERE key = 'mentions_model'")
    db.connect().commit()
    db.close()

    a, b = db.get_card('A'), db.get_card('B')
    assert a.related_names == []
    assert b.related_names == ['ALIAS A', 'not yet collected']
    assert db.get_meta('mentions_model') == '3'
    patch_card(b, related=['not yet collected'])
    assert not db.has_link(a.id, b.id)
    db.close()
    assert db.get_card('B').related_names == ['not yet collected']
    assert not db.has_link(a.id, b.id)


def test_upgrade_keeps_genuine_reciprocal_mentions(home):
    a = make_card('A', related_names=['B'])
    b = make_card('B', related_names=['A'])
    db.resolve_related(a)
    db.connect().execute("DELETE FROM meta WHERE key = 'mentions_model'")
    db.connect().commit()
    db.close()
    assert db.get_card('A').related_names == ['B']
    assert db.get_card('B').related_names == ['A']
    assert db.has_link(a.id, b.id)


def test_already_migrated_v2_is_not_destructively_reinterpreted(home):
    a = make_card('A', related_names=['B'])
    b = make_card('B', related_names=['A'])
    db.resolve_related(a)
    db.set_meta('mentions_model', '2')
    db.close()
    assert db.get_card('A').related_names == ['B']
    assert db.get_card('B').related_names == ['A']
    assert db.get_meta('mentions_model') == '2'
    assert db.has_link(a.id, b.id)


def test_failed_upgrade_rolls_back_mentions_and_marker_and_can_retry(home):
    import sqlite3
    import pytest
    from gloss.config import db_path

    a, b = make_card('A'), make_card('B')
    db.add_link(a.id, b.id)
    conn = db.connect()
    conn.execute("DELETE FROM meta WHERE key = 'mentions_model'")
    conn.execute("""CREATE TRIGGER fail_migration BEFORE UPDATE ON cards
                    WHEN old.term = 'B' BEGIN SELECT RAISE(ABORT, 'test failure'); END""")
    conn.commit()
    db.close()
    with pytest.raises(sqlite3.IntegrityError, match='test failure'):
        db.connect()
    assert db._conn is None
    with sqlite3.connect(db_path()) as inspect:
        assert inspect.execute('SELECT related_names FROM cards ORDER BY id').fetchall() == [('[]',), ('[]',)]
        assert inspect.execute("SELECT value FROM meta WHERE key = 'mentions_model'").fetchone() is None
        inspect.execute('DROP TRIGGER fail_migration')
    assert db.get_card('A').related_names == ['B']
    assert db.get_card('B').related_names == ['A']
    assert db.get_meta('mentions_model') == '3'

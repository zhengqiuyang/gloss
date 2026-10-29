"""gloss demo: seed sample cards, idempotency, and clean removal."""

from __future__ import annotations

import json

from click.testing import CliRunner

from gloss import db
from gloss.cli import cli
from gloss.demo import META_KEY, SPEC, clear_demo, seed_demo


def test_seed_demo_inserts_all_cards(runner: CliRunner, home):
    result = runner.invoke(cli, ["demo"])
    assert result.exit_code == 0, result.output
    cards = db.list_cards(limit=100)
    assert len(cards) == len(SPEC)
    rag = db.get_card("RAG")
    assert rag.hit_count == 4
    assert db.neighbors(rag.id)  # linked to Embedding / 向量数据库 / 幻觉


def test_seed_demo_is_idempotent(home):
    added, skipped = seed_demo()
    assert added == len(SPEC) and skipped == 0
    added, skipped = seed_demo()  # second run: nothing new, nothing duplicated
    assert added == 0 and skipped == len(SPEC)
    assert len(db.list_cards(limit=100)) == len(SPEC)


def test_seed_demo_skips_existing_user_cards(home):
    from conftest import make_card

    make_card("RAG", one_liner="我自己的卡片")
    added, skipped = seed_demo()
    assert added == len(SPEC) - 1 and skipped == 1
    assert db.get_card("RAG").one_liner == "我自己的卡片"  # user card untouched


def test_clear_demo_removes_only_demo_cards(home):
    from conftest import make_card

    make_card("我自己的词", one_liner="保留我")
    seed_demo()
    removed = clear_demo()
    assert removed == len(SPEC)
    assert len(db.list_cards(limit=100)) == 1
    assert db.get_card("我自己的词") is not None
    assert db.get_card("RAG") is None
    assert db.get_meta(META_KEY) == "[]"


def test_clear_before_seed_is_noop(home):
    assert clear_demo() == 0


def test_demo_links_cleaned_after_clear(home):
    seed_demo()
    rag = db.get_card("RAG")
    assert db.neighbors(rag.id)
    clear_demo()
    assert db.neighbors(rag.id) == []  # cascade removed the link rows

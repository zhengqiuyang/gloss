"""Data ownership: gloss rm (delete) and gloss edit (patch + editor flow)."""

from __future__ import annotations

import pytest

from gloss import db
from gloss.cli import cli

from conftest import make_card


# -- rm -----------------------------------------------------------------------

def test_rm_deletes_card_and_links(runner, home):
    a = make_card("RAG", one_liner="x")
    b = make_card("Embedding", one_liner="y")
    db.add_link(a.id, b.id)

    result = runner.invoke(cli, ["rm", "RAG", "-y"])
    assert result.exit_code == 0, result.output
    assert "已删除" in result.output
    assert db.get_card("RAG") is None
    assert db.neighbors(b.id) == []


def test_rm_prompts_without_yes(runner, home):
    make_card("RAG")
    result = runner.invoke(cli, ["rm", "RAG"], input="n\n")
    assert result.exit_code != 0
    assert db.get_card("RAG") is not None  # declined → kept

    result = runner.invoke(cli, ["rm", "RAG"], input="y\n")
    assert result.exit_code == 0, result.output
    assert db.get_card("RAG") is None


def test_rm_missing_card_is_friendly(runner, home):
    make_card("RAG")
    result = runner.invoke(cli, ["rm", "NOPE"])
    assert result.exit_code != 0
    assert "最近收录" in str(result.exception)


def test_rm_removes_aliases_too(runner, home):
    make_card("RAG", aliases=["检索增强生成"])
    runner.invoke(cli, ["rm", "检索增强生成", "-y"])
    assert db.get_card("检索增强生成") is None
    assert db.get_card("rag") is None


# -- edit: flag patches ---------------------------------------------------------

def test_edit_one_liner_and_detail(runner, home):
    make_card("RAG", one_liner="旧", detail="旧详情")
    result = runner.invoke(cli, ["edit", "RAG", "--one-liner", "新一句话", "--detail", "新详情"])
    assert result.exit_code == 0, result.output
    card = db.get_card("RAG")
    assert card.one_liner == "新一句话"
    assert card.detail == "新详情"


def test_edit_add_alias_tag_related(runner, home):
    make_card("RAG", one_liner="x")
    make_card("Embedding", one_liner="向量化")
    runner.invoke(cli, ["edit", "RAG", "--add-alias", "检索增强",
                        "--add-tag", "nlp", "--add-related", "Embedding"])
    card = db.get_card("RAG")
    assert "检索增强" in card.aliases
    assert "nlp" in card.tags
    assert db.has_link(db.get_card("RAG").id, db.get_card("Embedding").id)
    # alias repeated twice must not duplicate
    runner.invoke(cli, ["edit", "RAG", "--add-alias", "检索增强"])
    assert db.get_card("RAG").aliases.count("检索增强") == 1


def test_edit_add_related_unknown_parks_pending(runner, home):
    make_card("RAG", one_liner="x")
    runner.invoke(cli, ["edit", "RAG", "--add-related", "HNSW"])
    assert "HNSW" in db.get_card("RAG").related_names


def test_edit_term_rename_keeps_old_as_alias(runner, home):
    make_card("RAG", one_liner="x")
    result = runner.invoke(cli, ["edit", "RAG", "--term", "检索增强生成"])
    assert result.exit_code == 0, result.output
    assert db.get_card("RAG") is not None      # 旧名走别名仍可查
    assert db.get_card("检索增强生成").term == "检索增强生成"


def test_edit_term_rename_onto_existing_rejected(runner, home):
    make_card("RAG")
    make_card("LSP")
    result = runner.invoke(cli, ["edit", "RAG", "--term", "LSP"])
    assert result.exit_code != 0
    assert "冲突" in str(result.exception)
    assert db.get_card("RAG") is not None


# -- edit: $EDITOR flow ----------------------------------------------------------

def test_edit_editor_flow_applies_changes(runner, home, monkeypatch):
    make_card("RAG", one_liner="旧一句话", detail="旧详情",
              aliases=["检索增强"], tags=["nlp"], related_names=["Embedding"])
    make_card("Embedding", one_liner="向量化")

    import click

    def fake_edit(text, extension=None):
        assert "term: RAG" in text
        assert "旧一句话" in text
        return text.replace("term: RAG", "term: 检索增强生成") \
                   .replace("旧一句话", "新一句话") \
                   .replace("tags: nlp", "tags: nlp, agents") \
                   .replace("related: Embedding", "related: Embedding, HNSW")

    monkeypatch.setattr(click, "edit", fake_edit)
    result = runner.invoke(cli, ["edit", "RAG"])
    assert result.exit_code == 0, result.output

    card = db.get_card("检索增强生成")
    assert card is not None
    assert db.get_card("RAG") is not None          # 旧名别名
    assert card.one_liner == "新一句话"
    assert "agents" in card.tags
    assert "HNSW" in card.related_names            # 未收录 → 挂起
    assert db.has_link(card.id, db.get_card("Embedding").id)


def test_edit_editor_no_change_says_so(runner, home, monkeypatch):
    make_card("RAG", one_liner="x")
    import click

    monkeypatch.setattr(click, "edit", lambda text, extension=None: None)
    result = runner.invoke(cli, ["edit", "RAG"])
    assert result.exit_code == 0, result.output
    assert "未做修改" in result.output


def test_edit_editor_rejects_unknown_field(runner, home, monkeypatch):
    make_card("RAG", one_liner="x")
    import click

    monkeypatch.setattr(click, "edit",
                        lambda text, extension=None: text.replace("term: RAG", "foo: bar"))
    result = runner.invoke(cli, ["edit", "RAG"])
    assert result.exit_code != 0
    assert "无法识别的字段" in str(result.exception)
    assert db.get_card("RAG").one_liner == "x"     # 坏内容不落库

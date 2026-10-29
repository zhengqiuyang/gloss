"""End-to-end CLI behavior with the fake LLM."""

from __future__ import annotations

import json

from gloss import db
from gloss.config import config_path, gloss_home
from gloss.cli import cli

from conftest import make_card


def test_lookup_then_cache_hit(runner, home, fake_llm):
    result = runner.invoke(cli, ["RAG"])
    assert result.exit_code == 0, result.output
    assert "已收录新卡片" in result.output

    result = runner.invoke(cli, ["RAG"])
    assert result.exit_code == 0, result.output
    assert "cache hit" in result.output
    assert len(fake_llm.term_calls) == 1


def test_unknown_subcommand_routes_to_lookup(runner, home, fake_llm):
    result = runner.invoke(cli, ["retrieval", "augmented", "generation"])
    assert result.exit_code == 0, result.output
    assert fake_llm.term_calls[0]["term"] == "retrieval augmented generation"


def test_lookup_with_note_and_source(runner, home, fake_llm):
    result = runner.invoke(cli, ["RAG", "-n", "读文档时", "-s", "https://a.b"])
    assert result.exit_code == 0, result.output
    card = db.get_card("RAG")
    assert card.context == "读文档时"
    assert card.source == "https://a.b"


def test_piped_short_stdin_becomes_term(runner, home, fake_llm):
    result = runner.invoke(cli, [], input="QUIC\n")
    assert result.exit_code == 0, result.output
    assert db.get_card("QUIC") is not None


def test_piped_long_stdin_becomes_snippet(runner, home, fake_llm):
    result = runner.invoke(cli, [], input="这是一段足够长的阅读内容。" * 20)
    assert result.exit_code == 0, result.output
    assert "值得单独收录的词" in result.output


def test_ls_and_search(runner, home, fake_llm):
    runner.invoke(cli, ["RAG"])
    make_card("LSP", one_liner="语言服务器协议")
    out = runner.invoke(cli, ["ls"]).output
    assert "RAG" in out and "LSP" in out
    out = runner.invoke(cli, ["search", "语言服务器"]).output
    assert "LSP" in out


def test_show_without_model_call(runner, home, fake_llm):
    make_card("RAG", one_liner="本地已有")
    result = runner.invoke(cli, ["show", "RAG"])
    assert result.exit_code == 0
    assert len(fake_llm.term_calls) == 0
    result = runner.invoke(cli, ["show", "RAG", "--json"])
    data = json.loads(result.output)
    assert data["term"] == "RAG"


def test_related_command(runner, home, fake_llm):
    make_card("Embedding")
    runner.invoke(cli, ["RAG"])  # payload links Embedding, parks 向量数据库
    out = runner.invoke(cli, ["related", "RAG"]).output
    assert "Embedding" in out
    assert "向量数据库" in out


def test_review_and_stats(runner, home, fake_llm):
    runner.invoke(cli, ["RAG"])
    out = runner.invoke(cli, ["review"]).output
    assert "RAG" in out
    out = runner.invoke(cli, ["stats"]).output
    assert "1" in out


def test_config_set_and_show_masks_key(runner, home):
    result = runner.invoke(cli, ["config", "set", "llm.model", "glm-4-flash"])
    assert result.exit_code == 0, result.output
    assert "glm-4-flash" in config_path().read_text()

    runner.invoke(cli, ["config", "set", "llm.api_key", "sk-secret-12345678"])
    out = runner.invoke(cli, ["config", "show"]).output
    assert "sk-secret-12345678" not in out
    assert "sk-s…" in out or "…" in out


def test_config_rejects_unknown_key(runner, home):
    result = runner.invoke(cli, ["config", "set", "nope.key", "1"])
    assert result.exit_code != 0
    assert "未知配置项" in str(result.exception)


def test_export_json_and_md(runner, home, fake_llm, tmp_path):
    make_card("Embedding", one_liner="向量化")
    runner.invoke(cli, ["RAG"])  # links Embedding
    out_dir = home / "export"

    result = runner.invoke(cli, ["export", "--format", "json", "--out", str(out_dir / "b.json")])
    assert result.exit_code == 0, result.output
    payload = json.loads((out_dir / "b.json").read_text())
    terms = {c["term"] for c in payload["cards"]}
    assert {"RAG", "Embedding"} <= terms

    result = runner.invoke(cli, ["export", "--out", str(out_dir / "md")])
    assert result.exit_code == 0, result.output
    rag_md = (out_dir / "md" / "RAG.md").read_text()
    assert "term: RAG" in rag_md
    assert "[[Embedding]]" in rag_md
    assert (out_dir / "md" / "index.md").exists()


def test_show_without_args_gives_friendly_hint(runner, home):
    make_card("RAG", one_liner="x")
    result = runner.invoke(cli, ["show"])
    assert result.exit_code != 0
    msg = str(result.exception)
    assert "gloss show <术语>" in msg
    assert "RAG" in msg  # 最近收录的词出现在提示里


def test_related_without_args_gives_friendly_hint(runner, home):
    result = runner.invoke(cli, ["related"])
    assert result.exit_code != 0
    assert "gloss related <术语>" in str(result.exception)


def test_search_without_args_gives_friendly_hint(runner, home):
    result = runner.invoke(cli, ["search"])
    assert result.exit_code != 0
    assert "gloss search <关键词>" in str(result.exception)


def test_missing_api_key_gives_friendly_error(runner, home, monkeypatch):
    monkeypatch.delenv("GLOSS_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runner.invoke(cli, ["config", "set", "llm.api_key", ""])
    result = runner.invoke(cli, ["GLOSS-TERM"])
    assert result.exit_code != 0
    assert "GLOSS_API_KEY" in str(result.exception)
    # nothing half-written: the db stays empty
    assert db.get_card("GLOSS-TERM") is None

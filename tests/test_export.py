"""Markdown export: filename collisions, wiki-link mapping, YAML safety."""

from __future__ import annotations

from gloss import db, export

from conftest import make_card


def _export(home, tmp_path):
    out = tmp_path / "vault"
    export.export_markdown(out)
    return out


def test_index_term_does_not_take_the_index_file(home, tmp_path):
    make_card("index", one_liner="一张真的叫 index 的卡片", detail="正文")
    out = _export(home, tmp_path)
    idx = (out / "index.md").read_text(encoding="utf-8")
    assert "# Glossary" in idx  # 索引文件还是索引
    card = (out / "index-2.md").read_text(encoding="utf-8")
    assert "# index" in card  # 卡片也完整导出了，没被索引覆盖


def test_case_insensitive_sanitized_names_both_export(home, tmp_path):
    # "RAG:begin" → RAGbegin；"RAGBegin" 与之在大小写不敏感文件系统上同撞
    make_card("RAG:begin", one_liner="x", detail="y")
    make_card("RAGBegin", one_liner="x", detail="y")
    out = _export(home, tmp_path)
    assert "# RAG:begin" in (out / "RAGbegin.md").read_text(encoding="utf-8")
    assert "# RAGBegin" in (out / "RAGBegin-2.md").read_text(encoding="utf-8")


def test_special_characters_are_cleaned_but_card_survives(home, tmp_path):
    make_card('C++/Rust?"<>|', one_liner="x", detail="y")
    out = _export(home, tmp_path)
    assert (out / "C++Rust.md").exists()


def test_wikilinks_point_at_real_files(home, tmp_path):
    make_card("Embedding: vectors", one_liner="x", detail="y")
    rag = make_card("RAG", one_liner="x", detail="y",
                    related_names=["Embedding: vectors", "Faiss"])
    db.resolve_related(rag)
    out = _export(home, tmp_path)
    rag_md = (out / "RAG.md").read_text(encoding="utf-8")
    # 链接目标是真实文件名，显示文本保留原词名
    assert "[[Embedding-vectors|Embedding: vectors]]" in rag_md
    assert "**待收录**: Faiss" in rag_md  # 未收录的词保持纯文本
    idx = (out / "index.md").read_text(encoding="utf-8")
    assert "[[Embedding-vectors|Embedding: vectors]]" in idx


def test_yaml_fields_with_colons_quotes_newlines_survive(home, tmp_path):
    make_card(
        "A: B",
        one_liner='he said "hi": loudly',
        detail="正文里出现 --- 也不该破坏 YAML",
        source="url: https://x",
        aliases=["weird: name", 'quo"te', "multi\nline"],
    )
    out = _export(home, tmp_path)
    text = (out / "A-B.md").read_text(encoding="utf-8")
    assert 'term: "A: B"' in text
    assert 'source: "url: https://x"' in text
    assert '"weird: name"' in text
    assert "multi\\nline" in text  # 换行被转义而不是破坏结构
    try:
        import yaml
    except ImportError:
        return  # 没装 pyyaml 就只做结构断言
    import re
    front = re.match(r"^---\n(.*?)\n---\n", text, re.S).group(1)
    data = yaml.safe_load(front)
    assert data["term"] == "A: B"
    assert data["aliases"] == ["weird: name", 'quo"te', "multi\nline"]
    assert data["source"] == "url: https://x"

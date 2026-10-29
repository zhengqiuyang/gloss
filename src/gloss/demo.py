"""Seed/clear a sample glossary so new users can explore without an API key."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

from . import db
from .models import Card

META_KEY = "demo_card_ids"

SPEC: List[Dict[str, Any]] = [
    {
        "term": "RAG",
        "aliases": ["Retrieval-Augmented Generation", "检索增强生成"],
        "one_liner": "让模型先查资料再作答的技术。",
        "detail": "回答前先从外部知识库检索相关片段，拼进上下文交给大模型生成答案。能显著减少幻觉，也是 gloss 缓存机制想帮你省掉的重复劳动。",
        "tags": ["machine-learning", "nlp"],
        "related": ["Embedding", "向量数据库", "幻觉"],
        "hits": 4, "days_ago": 12,
        "source": "https://docs.example.com/agents",
        "context": "读 agent 架构文档时",
    },
    {
        "term": "Embedding",
        "aliases": ["嵌入向量", "向量表示"],
        "one_liner": "把文字变成一串数字（向量），让语义可以计算。",
        "detail": "语义相近的文本，向量距离也相近。是语义搜索和 RAG 的地基：先 embedding 再检索。",
        "tags": ["machine-learning", "nlp"],
        "related": ["向量数据库"],
        "hits": 2, "days_ago": 12,
        "source": "https://example.com/embeddings-guide",
    },
    {
        "term": "向量数据库",
        "aliases": ["vector database"],
        "one_liner": "专门存向量、按相似度检索的数据库。",
        "detail": "普通数据库查的是精确匹配，向量数据库查的是“最像的 K 个”。代表产品：Milvus、Qdrant、pgvector。gloss 未来的语义搜索就靠它。",
        "tags": ["database"],
        "related": ["HNSW", "Embedding"],
        "hits": 3, "days_ago": 10,
        "source": "https://example.com/vector-db-comparison",
    },
    {
        "term": "HNSW",
        "aliases": ["Hierarchical Navigable Small World"],
        "one_liner": "向量数据库里最快的近似最近邻搜索算法之一。",
        "detail": "把向量组织成分层的图，查询时从顶层粗跳、底层细找，用一点点准确率换百倍速度。Qdrant、Milvus 的默认索引。",
        "tags": ["algorithm", "database"],
        "related": ["向量数据库"],
        "hits": 1, "days_ago": 10,
        "source": "https://example.com/ann-algorithms",
    },
    {
        "term": "MCP",
        "aliases": ["Model Context Protocol"],
        "one_liner": "Anthropic 提出的开放协议，让 AI 应用统一接入外部工具和数据。",
        "detail": "把“工具调用”标准化成 client/server 协议：一个 MCP server 可以被 Claude、编辑器、agent 复用。gloss 的 roadmap 里就有一条：把词库暴露成 MCP server。",
        "tags": ["ai-agents", "protocol"],
        "related": ["RAG"],
        "hits": 2, "days_ago": 6,
        "source": "https://example.com/mcp-intro",
        "context": "看 Claude Desktop 发布时",
    },
    {
        "term": "QUIC",
        "aliases": [],
        "one_liner": "基于 UDP 的低延迟传输协议，HTTP/3 的底座。",
        "detail": "把 TCP 的可靠传输和 TLS 握手合并进 UDP 里实现，建连快、切换网络不断流。你刷视频觉得“换 Wi-Fi 不断”多半有它功劳。",
        "tags": ["network", "protocol"],
        "related": [],
        "hits": 0, "days_ago": 4,
        "source": "https://example.com/http3-explained",
    },
    {
        "term": "FTS5",
        "aliases": ["SQLite Full-Text Search 5"],
        "one_liner": "SQLite 内置的全文搜索模块，gloss 的搜索就是它。",
        "detail": "建倒排索引支持关键词检索，配合 bm25 排序。零部署、跟主库一个文件，个人工具首选。",
        "tags": ["database", "sqlite"],
        "related": [],
        "hits": 1, "days_ago": 3,
        "source": "https://example.com/sqlite-fts5",
    },
    {
        "term": "幻觉",
        "aliases": ["hallucination"],
        "one_liner": "大模型一本正经地编造事实的现象。",
        "detail": "模型在知识盲区会生成看似合理实则错误的内容。主流解法就是 RAG：让它先查资料再开口。",
        "tags": ["machine-learning", "nlp"],
        "related": ["RAG"],
        "hits": 1, "days_ago": 2,
        "source": "https://example.com/hallucination-survey",
    },
    {
        "term": "LlamaIndex 读书笔记",
        "type": "snippet",
        "one_liner": "LlamaIndex 的核心抽象是“索引 + 查询引擎”，对个人知识库场景非常契合。",
        "detail": "文章对比了 LlamaIndex 和 LangChain：前者专注数据接入和检索，开箱即用的 RAG 管线；后者更通用但样板代码多。提到 chunk 大小和 embedding 选型对效果影响很大。",
        "tags": ["ai-agents"],
        "related": [],
        "hits": 0, "days_ago": 1,
        "source": "pbpaste | gloss",
    },
]


def seed_demo() -> Tuple[int, int]:
    """Insert the sample cards (skipping terms you already have). Returns (added, skipped)."""
    added, skipped = 0, 0
    ids = json.loads(db.get_meta(META_KEY) or "[]")
    for s in SPEC:
        if db.get_card(s["term"]) is not None:
            skipped += 1
            continue
        ts = (datetime.now(timezone.utc) - timedelta(days=s["days_ago"])).isoformat(timespec="seconds")
        card = Card(
            term=s["term"],
            type=s.get("type", "term"),
            aliases=list(s.get("aliases", [])),
            one_liner=s["one_liner"],
            detail=s["detail"],
            tags=list(s.get("tags", [])),
            source=s.get("source", ""),
            context=s.get("context", ""),
            hit_count=s.get("hits", 0),
            created_at=ts,
            updated_at=ts,
        )
        db.insert_card(card)
        ids.append(card.id)
        added += 1
    for s in SPEC:
        card = db.get_card(s["term"])
        if card is None:
            continue
        for name in s.get("related", []):
            other = db.get_card(name)
            if other is not None and other.id != card.id:
                db.add_link(card.id, other.id)
    db.set_meta(META_KEY, json.dumps(ids))
    return added, skipped


def clear_demo() -> int:
    """Remove exactly the cards previously seeded by seed_demo()."""
    ids = json.loads(db.get_meta(META_KEY) or "[]")
    removed = 0
    for card_id in ids:
        if db.delete_card(card_id):
            removed += 1
    db.set_meta(META_KEY, "[]")
    return removed

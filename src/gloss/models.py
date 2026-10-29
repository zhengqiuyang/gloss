"""Core data model for glossary cards."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Optional

TYPE_TERM = "term"
TYPE_SNIPPET = "snippet"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def jdump(value: List[str]) -> str:
    return json.dumps(value, ensure_ascii=False)


def jload(raw: Any) -> List[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(v) for v in raw]
    try:
        data = json.loads(raw)
        return [str(v) for v in data] if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def norm_term(name: str) -> str:
    """Canonical lookup key for a term or alias."""
    return " ".join(name.strip().casefold().split())


@dataclass
class Card:
    """A single entry in the personal glossary.

    Two types exist:
    - ``term``: a concept glossed by the LLM (aliases, one-liner, detail, related).
    - ``snippet``: a digested piece of pasted text or an image, plus key terms
      the user might want to gloss later.
    """

    term: str
    type: str = TYPE_TERM
    aliases: List[str] = field(default_factory=list)
    one_liner: str = ""
    detail: str = ""
    tags: List[str] = field(default_factory=list)
    related_names: List[str] = field(default_factory=list)  # not-yet-linked names
    source: str = ""
    context: str = ""
    tokens_used: int = 0
    hit_count: int = 0
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    id: Optional[int] = None

    @property
    def norm(self) -> str:
        return norm_term(self.term)

    @property
    def all_names(self) -> List[str]:
        return [self.term] + list(self.aliases)

    @classmethod
    def from_row(cls, row) -> "Card":
        return cls(
            id=row["id"],
            type=row["type"],
            term=row["term"],
            aliases=jload(row["aliases"]),
            one_liner=row["one_liner"],
            detail=row["detail"],
            tags=jload(row["tags"]),
            related_names=jload(row["related_names"]),
            source=row["source"],
            context=row["context"],
            tokens_used=row["tokens_used"],
            hit_count=row["hit_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def to_row(self) -> dict:
        return {
            "type": self.type,
            "term": self.term,
            "aliases": jdump(self.aliases),
            "one_liner": self.one_liner,
            "detail": self.detail,
            "tags": jdump(self.tags),
            "related_names": jdump(self.related_names),
            "source": self.source,
            "context": self.context,
            "tokens_used": self.tokens_used,
            "hit_count": self.hit_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class GlossError(RuntimeError):
    """User-facing error with a clean message (no traceback)."""

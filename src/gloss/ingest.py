"""The lookup-or-ingest flow: local cache first, LLM only on a miss.

These functions are UI-free: they return result dicts so the CLI, the local
web server (and future entry points) can present however they like.
Result shapes:
  term:       {"card": Card, "cache_hit": bool, "linked": [Card],
               "pending": [str], "backfilled": [Card]}
  snippet:    {"card": Card, "key_terms": [str], "cache_hit": False}
  translate:  {"source": str, "translation": str, "target": str,
               "cache_hit": bool, "hits": int, "tokens": int}
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional

from . import db
from . import llm as llm_mod
from .config import load_config
from .models import Card, GlossError, TYPE_SNIPPET, TYPE_TERM, norm_term, now_iso

# A "term" is short, single-line input; anything longer is a snippet to digest.
TERM_MAX_CHARS = 80


def _known_terms() -> List[str]:
    cfg = load_config()
    return db.all_terms(limit=cfg["glossary"]["max_known_terms"])


def lookup_or_ingest(
    term: str,
    note: str = "",
    source: str = "",
    refresh: bool = False,
) -> Dict[str, Any]:
    term = " ".join(term.split())
    if not term:
        raise GlossError("请提供一个术语")
    existing = db.get_card(term)
    if existing and not refresh:
        db.increment_hit(existing.id)
        existing.hit_count += 1  # keep the in-memory card in sync for display
        return {"card": existing, "cache_hit": True, "linked": [], "pending": [], "backfilled": []}

    known = _known_terms()
    data, tokens = llm_mod.get_client().explain_term(term, known, context=note, source=source)
    card = _card_from_llm(data, term=term, note=note, source=source, tokens=tokens)
    if norm_term(card.term) != norm_term(term) and db.get_card(card.term) is not None:
        # The LLM's normalized name collides with an existing card; keep the
        # user's spelling as the canonical term and the payload name as an alias.
        payload_name = card.term
        card.term = term
        card.aliases = [a for a in card.aliases if norm_term(a) != norm_term(term)]
        if norm_term(payload_name) not in map(norm_term, card.aliases):
            card.aliases.insert(0, payload_name)

    linked, pending = [], []
    if existing:  # --refresh keeps identity and history, replaces content
        existing.aliases = card.aliases
        existing.one_liner = card.one_liner
        existing.detail = card.detail
        existing.tags = card.tags
        existing.related_names = card.related_names
        existing.source = source or existing.source
        existing.context = note or existing.context
        existing.tokens_used += tokens
        existing.updated_at = now_iso()
        db.update_card(existing)
        card = existing
    else:
        db.insert_card(card)

    linked, pending = db.resolve_related(card)
    backfilled = db.backfill_links(card.id, card.norm) if card.id else []
    return {"card": card, "cache_hit": False, "linked": linked, "pending": pending, "backfilled": backfilled}


def _card_from_llm(
    data: Dict[str, Any],
    term: str,
    note: str,
    source: str,
    tokens: int,
) -> Card:
    aliases = _as_list(data.get("aliases"))
    name = str(data.get("term") or term).strip() or term
    # The LLM may normalize the name; keep the user's spelling as an alias.
    if norm_term(name) != norm_term(term) and norm_term(term) not in map(norm_term, aliases):
        aliases.insert(0, term)
    return Card(
        term=name,
        type=TYPE_TERM,
        aliases=aliases,
        one_liner=str(data.get("one_liner") or "").strip(),
        detail=str(data.get("detail") or "").strip(),
        tags=[t.lower().strip() for t in _as_list(data.get("tags"))],
        related_names=_as_list(data.get("related")),
        source=source,
        context=note,
        tokens_used=tokens,
    )


def _as_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def ingest_piped(text: str) -> Dict[str, Any]:
    """Route pasted text: short single line → term lookup, longer → snippet digest."""
    text = text.strip()
    if not text:
        raise GlossError("没有从管道读到内容")
    if len(text) <= TERM_MAX_CHARS and "\n" not in text:
        return lookup_or_ingest(text)
    known = _known_terms()
    data, tokens = llm_mod.get_client().digest_text(text, known)
    title = str(data.get("title") or "").strip() or text[:40]
    title = _unique_title(title)
    card = Card(
        term=title,
        type=TYPE_SNIPPET,
        one_liner=str(data.get("one_liner") or "").strip(),
        detail=str(data.get("detail") or "").strip(),
        tags=[t.lower().strip() for t in _as_list(data.get("tags"))],
        related_names=[],
        tokens_used=tokens,
    )
    db.insert_card(card)
    return {"card": card, "key_terms": _as_list(data.get("key_terms")), "cache_hit": False}


def ingest_image(image_path: str, note: str = "") -> Dict[str, Any]:
    known = _known_terms()
    data, tokens = llm_mod.get_client().analyze_image(image_path, note, known)
    title = str(data.get("title") or "").strip() or "image snippet"
    title = _unique_title(title)
    card = Card(
        term=title,
        type=TYPE_SNIPPET,
        one_liner=str(data.get("one_liner") or "").strip(),
        detail=str(data.get("detail") or "").strip(),
        tags=[t.lower().strip() for t in _as_list(data.get("tags"))],
        source=image_path,
        context=note,
        tokens_used=tokens,
    )
    db.insert_card(card)
    return {"card": card, "key_terms": _as_list(data.get("key_terms")), "cache_hit": False}


def _unique_title(title: str) -> str:
    title = re.sub(r"\s+", " ", title)[:80].strip()
    candidate, n = title, 2
    while db.get_card(candidate):
        candidate = f"{title} ({n})"
        n += 1
    return candidate


def translate_text(text: str) -> Dict[str, Any]:
    """Translate text into the configured target language, cached forever."""
    from .config import load_config

    text = text.strip()
    if not text:
        raise GlossError("没有可翻译的内容")
    target = load_config()["glossary"]["translate_target"]
    hash_ = hashlib.sha1(f"{target}\n{text}".encode()).hexdigest()[:16]

    cached = db.get_translation(hash_)
    if cached:
        db.increment_translation_hit(cached["id"])
        return {
            "source": cached["source"],
            "translation": cached["translation"],
            "target": target,
            "cache_hit": True,
            "hits": cached["hit_count"] + 1,
            "tokens": 0,
        }

    translation, tokens = llm_mod.get_client().translate(text, target)
    row = db.insert_translation(hash_, text, target, translation, tokens)
    return {
        "source": row["source"],
        "translation": row["translation"],
        "target": target,
        "cache_hit": False,
        "hits": 0,
        "tokens": tokens,
    }


def patch_card(
    card: Card,
    *,
    term: Optional[str] = None,
    one_liner: Optional[str] = None,
    detail: Optional[str] = None,
    source: Optional[str] = None,
    context: Optional[str] = None,
    aliases: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    related: Optional[List[str]] = None,
    additive: bool = False,
) -> Dict[str, Any]:
    """Patch card fields (UI-free). None leaves a field untouched.

    additive=True extends alias/tag/related lists instead of replacing them.
    Renaming keeps the old name as an alias; renaming onto an existing card
    is rejected. After saving, related names re-resolve (new links, pending
    backfill) exactly like a fresh ingest.
    """
    old_term: Optional[str] = None
    if term is not None:
        new = term.strip()
        if not new:
            raise GlossError("词条名不能为空")
        if norm_term(new) != card.norm:
            other = db.get_card(new)
            if other is not None and other.id != card.id:
                raise GlossError(f"词库里已有 {other.term!r}，改名会冲突")
            old_term = card.term
            card.term = new

    def merge(current: List[str], items: List[str], mode: str) -> List[str]:
        items = [i.strip() for i in items if i and i.strip()]
        if mode == "replace":
            return items
        out = list(current)
        for i in items:
            if norm_term(i) not in map(norm_term, out):
                out.append(i)
        return out

    mode = "extend" if additive else "replace"
    if one_liner is not None:
        card.one_liner = one_liner.strip()
    if detail is not None:
        card.detail = detail.strip()
    if source is not None:
        card.source = source.strip()
    if context is not None:
        card.context = context.strip()
    if aliases is not None:
        card.aliases = merge(card.aliases, aliases, mode)
    if tags is not None:
        card.tags = [t.lower() for t in merge(card.tags, tags, mode)]
    if related is not None:
        card.related_names = merge(card.related_names, related, mode)

    # 旧名转别名放在最后，避免被 aliases 的整体替换冲掉
    if old_term is not None and norm_term(old_term) not in map(norm_term, card.aliases):
        card.aliases.insert(0, old_term)

    card.updated_at = now_iso()
    db.update_card(card)
    linked, pending = db.resolve_related(card)
    backfilled = db.backfill_links(card.id, card.norm)
    return {"card": card, "cache_hit": False, "linked": linked,
            "pending": pending, "backfilled": backfilled}

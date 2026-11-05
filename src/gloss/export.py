"""Export the glossary: Obsidian-friendly markdown files or a JSON backup."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List

from . import db
from .models import Card

# The vault index file lives at index.md; no card may take that name.
INDEX_NAME = "index"


def export_markdown(out_dir: Path) -> List[Path]:
    out_dir = out_dir.expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    cards = list(db.iter_all())
    names = _all_filenames(cards)  # card id → file base name, wiki links use this
    written: List[Path] = []
    for card in cards:
        path = out_dir / (names[card.id] + ".md")
        path.write_text(_card_markdown(card, names), encoding="utf-8")
        written.append(path)
    index = out_dir / (INDEX_NAME + ".md")
    index.write_text(_index_markdown(names), encoding="utf-8")
    written.append(index)
    return written


def export_json(out_path: Path) -> Path:
    out_path = out_path.expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cards = []
    for card in db.iter_all():
        data = card.to_row()
        data["term_norm"] = card.norm
        data["links"] = [c.term for c in db.neighbors(card.id)]
        cards.append(data)
    payload = {"format": "gloss-backup", "version": 1, "cards": cards}
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def _sanitize_filename(term: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]', "", term).strip().replace(" ", "-")
    # Windows keeps trailing dots/spaces out of filenames; macOS strips dots.
    return cleaned.rstrip(". ") or "untitled"


def _all_filenames(cards: List[Card]) -> Dict[int, str]:
    """One filename per card, unique on case-insensitive filesystems.

    macOS/Windows FS treat `Index.md` and `index.md` as the same file, so
    collisions are detected on casefolded names while the real spelling is
    kept for the file itself. Cards colliding with each other (or with the
    reserved index name) get a numeric suffix.
    """
    used_cf = {INDEX_NAME}
    names: Dict[int, str] = {}
    for card in cards:
        base = _sanitize_filename(card.term)
        candidate, n = base, 2
        while candidate.casefold() in used_cf:
            candidate = f"{base}-{n}"
            n += 1
        used_cf.add(candidate.casefold())
        names[card.id] = candidate
    return names


def _yaml_str(value: str) -> str:
    """YAML-safe double-quoted scalar (JSON string syntax is valid YAML)."""
    return json.dumps(str(value), ensure_ascii=False)


def _yaml_list(name: str, items: List[str]) -> str:
    if not items:
        return ""
    lines = "\n".join(f"  - {_yaml_str(i)}" for i in items)
    return f"{name}:\n{lines}\n"


def _card_markdown(card: Card, names: Dict[int, str]) -> str:
    linked = db.neighbors(card.id)
    front = [
        "---",
        f"term: {_yaml_str(card.term)}",
        "type: " + card.type,
    ]
    if card.aliases:
        front.append(_yaml_list("aliases", card.aliases).rstrip("\n"))
    if card.tags:
        front.append(_yaml_list("tags", card.tags).rstrip("\n"))
    front += [
        f"created: {card.created_at}",
        f"hits: {card.hit_count}",
    ]
    if card.source:
        front.append(f"source: {_yaml_str(card.source)}")
    front.append("---\n")
    body = f"# {card.term}\n\n"
    if card.one_liner:
        body += f"**{card.one_liner}**\n\n"
    if card.detail:
        body += f"{card.detail}\n\n"
    if linked:
        # [[filename|term]]: the link target is the real file, the display
        # text keeps the user's original spelling.
        body += (
            "**相关概念**: "
            + ", ".join(f"[[{names[c.id]}|{c.term}]]" for c in linked)
            + "\n\n"
        )
    if card.related_names:
        pending = db.pending_names(card)
        if pending:
            body += (
                "**待收录**: "
                + ", ".join(pending)
                + "\n\n"
            )
    if card.context:
        body += f"> 语境: {card.context}\n"
    return "\n".join(front) + "\n" + body


def _index_markdown(names: Dict[int, str]) -> str:
    by_tag: dict = {}
    for card in db.iter_all():
        for tag in card.tags or ["未分类"]:
            by_tag.setdefault(tag, []).append(card)
    lines = ["# Glossary", ""]
    for tag, cards in sorted(by_tag.items()):
        lines.append(f"## #{tag}\n")
        for c in cards:
            lines.append(f"- [[{names[c.id]}|{c.term}]] — {c.one_liner}")
        lines.append("")
    return "\n".join(lines)

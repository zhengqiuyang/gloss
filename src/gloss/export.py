"""Export the glossary: Obsidian-friendly markdown files or a JSON backup."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List

from . import db
from .models import Card


def export_markdown(out_dir: Path) -> List[Path]:
    out_dir = out_dir.expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    used: set = set()
    for card in db.iter_all():
        path = out_dir / (_filename(card, used) + ".md")
        path.write_text(_card_markdown(card), encoding="utf-8")
        written.append(path)
    index = out_dir / "index.md"
    index.write_text(_index_markdown(), encoding="utf-8")
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


def _filename(card: Card, used: set) -> str:
    base = re.sub(r"[\\/:*?\"<>|]", "", card.term).strip().replace(" ", "-") or "untitled"
    candidate, n = base, 2
    while candidate in used:
        candidate = f"{base}-{n}"
        n += 1
    used.add(candidate)
    return candidate


def _yaml_list(name: str, items: List[str]) -> str:
    if not items:
        return ""
    lines = "\n".join(f"  - {i}" for i in items)
    return f"{name}:\n{lines}\n"


def _card_markdown(card: Card) -> str:
    linked = db.neighbors(card.id)
    front = [
        "---",
        f"term: {card.term}",
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
        front.append(f"source: {card.source}")
    front.append("---\n")
    body = f"# {card.term}\n\n"
    if card.one_liner:
        body += f"**{card.one_liner}**\n\n"
    if card.detail:
        body += f"{card.detail}\n\n"
    if linked:
        body += "**相关概念**: " + ", ".join(f"[[{c.term}]]" for c in linked) + "\n\n"
    if card.related_names:
        body += (
            "**待收录**: "
            + ", ".join(card.related_names)
            + "\n\n"
        )
    if card.context:
        body += f"> 语境: {card.context}\n"
    return "\n".join(front) + "\n" + body


def _index_markdown() -> str:
    by_tag: dict = {}
    for card in db.iter_all():
        for tag in card.tags or ["未分类"]:
            by_tag.setdefault(tag, []).append(card)
    lines = ["# Glossary", ""]
    for tag, cards in sorted(by_tag.items()):
        lines.append(f"## #{tag}\n")
        for c in cards:
            lines.append(f"- [[{c.term}]] — {c.one_liner}")
        lines.append("")
    return "\n".join(lines)

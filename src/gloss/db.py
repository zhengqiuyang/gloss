"""SQLite storage: cards, FTS5 search, undirected concept links.

The FTS5 index uses external content + triggers so the cards table stays the
single source of truth. If the runtime SQLite lacks FTS5, gloss degrades to
LIKE-based search instead of failing.
"""

from __future__ import annotations

import sqlite3
from typing import Iterable, List, Optional, Tuple

from .models import Card, now_iso, norm_term

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY,
    type TEXT NOT NULL DEFAULT 'term',
    term TEXT NOT NULL,
    term_norm TEXT NOT NULL UNIQUE,
    aliases TEXT NOT NULL DEFAULT '[]',
    one_liner TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    related_names TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT '',
    context TEXT NOT NULL DEFAULT '',
    tokens_used INTEGER NOT NULL DEFAULT 0,
    hit_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alias_index (
    norm TEXT PRIMARY KEY,
    card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS links (
    card_a INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    card_b INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (card_a, card_b)
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS translations (
    id INTEGER PRIMARY KEY,
    hash TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    target_lang TEXT NOT NULL,
    translation TEXT NOT NULL,
    tokens_used INTEGER NOT NULL DEFAULT 0,
    hit_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""

_FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS cards_fts USING fts5(
    term, aliases, one_liner, detail, tags,
    content='cards', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS cards_ai AFTER INSERT ON cards BEGIN
    INSERT INTO cards_fts(rowid, term, aliases, one_liner, detail, tags)
    VALUES (new.id, new.term, new.aliases, new.one_liner, new.detail, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS cards_ad AFTER DELETE ON cards BEGIN
    INSERT INTO cards_fts(cards_fts, rowid, term, aliases, one_liner, detail, tags)
    VALUES ('delete', old.id, old.term, old.aliases, old.one_liner, old.detail, old.tags);
END;
CREATE TRIGGER IF NOT EXISTS cards_au AFTER UPDATE ON cards BEGIN
    INSERT INTO cards_fts(cards_fts, rowid, term, aliases, one_liner, detail, tags)
    VALUES ('delete', old.id, old.term, old.aliases, old.one_liner, old.detail, old.tags);
    INSERT INTO cards_fts(rowid, term, aliases, one_liner, detail, tags)
    VALUES (new.id, new.term, new.aliases, new.one_liner, new.detail, new.tags);
END;
"""

_conn: Optional[sqlite3.Connection] = None
_fts_enabled = True


def connect() -> sqlite3.Connection:
    global _conn, _fts_enabled
    if _conn is not None:
        return _conn
    from .config import db_path

    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: `gloss serve` handles requests in the server
    # thread while tests/CLI touch the same connection from other threads.
    # CPython's sqlite3 is built in serialized mode, so this is safe here.
    _conn = sqlite3.connect(path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA foreign_keys = ON")
    _conn.executescript(_SCHEMA)
    try:
        _conn.executescript(_FTS_DDL)
        _fts_enabled = True
    except sqlite3.OperationalError:
        _fts_enabled = False
    return _conn


def close() -> None:
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------

def insert_card(card: Card) -> Card:
    conn = connect()
    row = card.to_row()
    row["term_norm"] = card.norm
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    cur = conn.execute(f"INSERT INTO cards ({cols}) VALUES ({marks})", list(row.values()))
    conn.executemany(
        "INSERT OR IGNORE INTO alias_index (norm, card_id) VALUES (?, ?)",
        [(norm_term(a), cur.lastrowid) for a in card.aliases if norm_term(a)],
    )
    conn.commit()
    card.id = cur.lastrowid
    return card


def update_card(card: Card) -> None:
    conn = connect()
    row = card.to_row()
    row["term_norm"] = card.norm
    row["updated_at"] = now_iso()
    sets = ", ".join(f"{k} = ?" for k in row)
    conn.execute(f"UPDATE cards SET {sets} WHERE id = ?", list(row.values()) + [card.id])
    conn.execute("DELETE FROM alias_index WHERE card_id = ?", (card.id,))
    conn.executemany(
        "INSERT OR IGNORE INTO alias_index (norm, card_id) VALUES (?, ?)",
        [(norm_term(a), card.id) for a in card.aliases if norm_term(a)],
    )
    conn.commit()


def get_card(name: str) -> Optional[Card]:
    """Exact lookup by term or alias, case-insensitive."""
    key = norm_term(name)
    row = connect().execute(
        "SELECT * FROM cards WHERE term_norm = ?", (key,)
    ).fetchone()
    if row is None:
        row = connect().execute(
            """SELECT c.* FROM cards c JOIN alias_index a ON a.card_id = c.id
               WHERE a.norm = ?""",
            (key,),
        ).fetchone()
    return Card.from_row(row) if row else None


def get_card_by_id(card_id: int) -> Optional[Card]:
    row = connect().execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    return Card.from_row(row) if row else None


def delete_card(card_id: int) -> bool:
    """Delete a card; cascades its links and alias index via foreign keys."""
    cur = connect().execute("DELETE FROM cards WHERE id = ?", (card_id,))
    connect().commit()
    return cur.rowcount > 0


def get_meta(key: str) -> Optional[str]:
    row = connect().execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


# ---------------------------------------------------------------------------
# Translation cache (separate from the glossary on purpose: translated
# sentences are content, not terms — they shouldn't pollute FTS or graphs)
# ---------------------------------------------------------------------------

def get_translation(hash_: str) -> Optional[dict]:
    row = connect().execute(
        "SELECT * FROM translations WHERE hash = ?", (hash_,)
    ).fetchone()
    return dict(row) if row else None


def insert_translation(hash_: str, source: str, target_lang: str,
                       translation: str, tokens: int) -> dict:
    conn = connect()
    conn.execute(
        """INSERT INTO translations (hash, source, target_lang, translation,
                                     tokens_used, hit_count, created_at)
           VALUES (?, ?, ?, ?, ?, 0, ?)""",
        (hash_, source, target_lang, translation, tokens, now_iso()),
    )
    conn.commit()
    return get_translation(hash_)


def increment_translation_hit(id_: int) -> None:
    conn = connect()
    conn.execute(
        "UPDATE translations SET hit_count = hit_count + 1 WHERE id = ?", (id_,)
    )
    conn.commit()


def set_meta(key: str, value: str) -> None:
    connect().execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    connect().commit()


def increment_hit(card_id: int) -> None:
    conn = connect()
    conn.execute(
        "UPDATE cards SET hit_count = hit_count + 1 WHERE id = ?", (card_id,)
    )
    conn.commit()


def list_cards(
    card_type: Optional[str] = None,
    tag: Optional[str] = None,
    limit: int = 50,
) -> List[Card]:
    query = "SELECT * FROM cards"
    clauses, params = [], []
    if card_type:
        clauses.append("type = ?")
        params.append(card_type)
    if tag:
        clauses.append("tags LIKE ?")
        params.append(f'%"{tag}"%')
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(limit)
    return [Card.from_row(r) for r in connect().execute(query, params)]


def all_terms(limit: int = 200) -> List[str]:
    rows = connect().execute(
        "SELECT term FROM cards ORDER BY hit_count DESC, id DESC LIMIT ?", (limit,)
    )
    return [r["term"] for r in rows]


# ---------------------------------------------------------------------------
# Search (FTS5 with LIKE fallback and CJK support)
# ---------------------------------------------------------------------------

def _sanitize_fts(query: str) -> str:
    tokens = [t.replace('"', " ").strip() for t in query.split()]
    return " ".join(f'"{t}"' for t in tokens if t)


def _like_search(query: str, limit: int) -> List[Card]:
    pattern = f"%{query}%"
    rows = connect().execute(
        """SELECT * FROM cards
           WHERE term LIKE ? OR aliases LIKE ? OR one_liner LIKE ?
              OR detail LIKE ? OR tags LIKE ? OR context LIKE ?
           ORDER BY hit_count DESC, created_at DESC LIMIT ?""",
        (pattern,) * 6 + (limit,),
    )
    return [Card.from_row(r) for r in rows]


def search(query: str, limit: int = 20) -> List[Card]:
    results: List[Card] = []
    seen: set = set()
    if _fts_enabled:
        try:
            rows = connect().execute(
                """SELECT c.* FROM cards_fts f JOIN cards c ON c.id = f.rowid
                   WHERE cards_fts MATCH ?
                   ORDER BY bm25(cards_fts) LIMIT ?""",
                (_sanitize_fts(query), limit),
            )
            for r in rows:
                card = Card.from_row(r)
                seen.add(card.id)
                results.append(card)
        except sqlite3.OperationalError:
            pass  # malformed match expression — fall through to LIKE
    for card in _like_search(query, limit):
        if card.id not in seen:
            seen.add(card.id)
            results.append(card)
    return results[:limit]


# ---------------------------------------------------------------------------
# Links (undirected) and pending related-name backfill
# ---------------------------------------------------------------------------

def add_link(a_id: int, b_id: int) -> bool:
    """Link two cards; returns False if the link already existed."""
    if a_id == b_id:
        return False
    lo, hi = min(a_id, b_id), max(a_id, b_id)
    cur = connect().execute(
        "INSERT OR IGNORE INTO links (card_a, card_b, created_at) VALUES (?, ?, ?)",
        (lo, hi, now_iso()),
    )
    connect().commit()
    return cur.rowcount > 0


def neighbors(card_id: int) -> List[Card]:
    rows = connect().execute(
        """SELECT c.* FROM links l
           JOIN cards c ON c.id = CASE WHEN l.card_a = ? THEN l.card_b ELSE l.card_a END
           WHERE l.card_a = ? OR l.card_b = ?
           ORDER BY c.term""",
        (card_id, card_id, card_id),
    )
    return [Card.from_row(r) for r in rows]


def has_link(a_id: int, b_id: int) -> bool:
    lo, hi = min(a_id, b_id), max(a_id, b_id)
    row = connect().execute(
        "SELECT 1 FROM links WHERE card_a = ? AND card_b = ?", (lo, hi)
    ).fetchone()
    return row is not None


def resolve_related(card: Card) -> Tuple[List[Card], List[str]]:
    """Split the card's related_names into locally-linked cards and pending names.

    Pending names are persisted back on the card so a future card with that
    term can be back-linked (see backfill_links).
    """
    linked: List[Card] = []
    pending: List[str] = []
    for name in card.related_names:
        other = get_card(name)
        if other and other.id != card.id:
            add_link(card.id, other.id)
            linked.append(other)
        else:
            pending.append(name)
    if pending != card.related_names:
        card.related_names = pending
        update_card(card)
    return linked, pending


def backfill_links(card_id: int, norm: str) -> List[Card]:
    """After adding `card_id`, link it to cards whose pending related_names mention it."""
    newly_linked: List[Card] = []
    for row in connect().execute(
        "SELECT id, related_names FROM cards WHERE id != ? AND related_names LIKE ?",
        (card_id, f'%"{norm}"%'),
    ).fetchall():
        other = Card.from_row(connect().execute(
            "SELECT * FROM cards WHERE id = ?", (row["id"],)
        ).fetchone())
        names = [n for n in other.related_names if norm_term(n) != norm]
        if len(names) != len(other.related_names):
            other.related_names = names
            update_card(other)
            add_link(card_id, other.id)
            newly_linked.append(other)
    return newly_linked


# ---------------------------------------------------------------------------
# Stats / review
# ---------------------------------------------------------------------------

def counts() -> dict:
    conn = connect()
    total = conn.execute("SELECT COUNT(*) AS n FROM cards").fetchone()["n"]
    by_type = {
        r["type"]: r["n"]
        for r in conn.execute("SELECT type, COUNT(*) AS n FROM cards GROUP BY type")
    }
    total_hits = conn.execute("SELECT COALESCE(SUM(hit_count), 0) AS n FROM cards").fetchone()["n"]
    total_tokens = conn.execute(
        "SELECT COALESCE(SUM(tokens_used), 0) AS n FROM cards"
    ).fetchone()["n"]
    return {
        "total": total,
        "by_type": by_type,
        "cache_hits": total_hits,
        "tokens_spent": total_tokens,
    }


def created_since(days: int) -> List[Card]:
    rows = connect().execute(
        """SELECT * FROM cards
           WHERE created_at >= datetime('now', ?)
           ORDER BY created_at DESC""",
        (f"-{int(days)} days",),
    )
    return [Card.from_row(r) for r in rows]


def top_hits(limit: int = 5) -> List[Card]:
    rows = connect().execute(
        "SELECT * FROM cards WHERE hit_count > 0 ORDER BY hit_count DESC LIMIT ?",
        (limit,),
    )
    return [Card.from_row(r) for r in rows]


def iter_all() -> Iterable[Card]:
    for row in connect().execute("SELECT * FROM cards ORDER BY term"):
        yield Card.from_row(row)

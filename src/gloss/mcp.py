"""Read-only MCP server over stdio JSON-RPC 2.0 (zero extra runtime dependencies)."""

from __future__ import annotations

import json
import sys
from typing import Any, Callable, TextIO

from . import __version__, db
from .models import Card

DEFAULT_PROTOCOL_VERSION = "2024-11-05"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "gloss_search",
        "description": (
            "Search the user's local glossary cards by keyword "
            "(full-text FTS5 with LIKE fallback)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (term, alias, Chinese/English keywords, or tag).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of matching cards to return (1-50, default 10).",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 50,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "gloss_get",
        "description": (
            "Get a single glossary card by exact term or alias (case-insensitive), "
            "including linked neighbors and pending related concepts."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "term": {
                    "type": "string",
                    "description": "Exact term or alias to look up.",
                },
            },
            "required": ["term"],
            "additionalProperties": False,
        },
    },
    {
        "name": "gloss_related",
        "description": (
            "List linked glossary cards and pending (not yet glossed) related concepts "
            "for a given term or alias."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "term": {
                    "type": "string",
                    "description": "Exact term or alias to inspect links for.",
                },
            },
            "required": ["term"],
            "additionalProperties": False,
        },
    },
    {
        "name": "gloss_recent",
        "description": "List recently added glossary cards within the last N days.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Lookback window in days (1-365, default 7).",
                    "default": 7,
                    "minimum": 1,
                    "maximum": 365,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of cards to return (1-100, default 20).",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 100,
                },
            },
            "additionalProperties": False,
        },
    },
]


def _card_dict(card: Card, *, include_links: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        "term": card.term,
        "type": card.type,
        "aliases": list(card.aliases),
        "one_liner": card.one_liner,
        "detail": card.detail,
        "tags": list(card.tags),
        "source": card.source,
        "context": card.context,
        "hit_count": card.hit_count,
        "created_at": card.created_at,
        "updated_at": card.updated_at,
    }
    if include_links and card.id is not None:
        data["linked"] = [c.term for c in db.neighbors(card.id)]
        data["pending"] = db.pending_names(card)
    return data


def _require_nonempty_str(args: dict[str, Any], key: str) -> str:
    val = args.get(key)
    if not isinstance(val, str) or not val.strip():
        raise ValueError(f"参数 {key!r} 必须是非空字符串")
    return val.strip()


def _optional_int(args: dict[str, Any], key: str, default: int, lo: int, hi: int) -> int:
    if key not in args or args[key] is None:
        return default
    val = args[key]
    if isinstance(val, bool) or not isinstance(val, int):
        raise ValueError(f"参数 {key!r} 必须是整数")
    return max(lo, min(hi, val))


def _tool_search(args: dict[str, Any]) -> dict[str, Any]:
    query = _require_nonempty_str(args, "query")
    limit = _optional_int(args, "limit", default=10, lo=1, hi=50)
    cards = db.search(query, limit=limit)
    return {
        "query": query,
        "count": len(cards),
        "cards": [_card_dict(c) for c in cards],
    }


def _tool_get(args: dict[str, Any]) -> dict[str, Any]:
    term = _require_nonempty_str(args, "term")
    card = db.get_card(term)
    if card is None:
        return {"found": False, "term": term, "message": "未找到该卡片"}
    return {"found": True, "card": _card_dict(card, include_links=True)}


def _tool_related(args: dict[str, Any]) -> dict[str, Any]:
    term = _require_nonempty_str(args, "term")
    card = db.get_card(term)
    if card is None or card.id is None:
        return {"found": False, "term": term, "linked": [], "pending": []}
    return {
        "found": True,
        "term": card.term,
        "linked": [
            {"term": c.term, "one_liner": c.one_liner}
            for c in db.neighbors(card.id)
        ],
        "pending": db.pending_names(card),
    }


def _tool_recent(args: dict[str, Any]) -> dict[str, Any]:
    days = _optional_int(args, "days", default=7, lo=1, hi=365)
    limit = _optional_int(args, "limit", default=20, lo=1, hi=100)
    cards = db.created_since(days)[:limit]
    return {
        "days": days,
        "count": len(cards),
        "cards": [_card_dict(c) for c in cards],
    }


_TOOL_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "gloss_search": _tool_search,
    "gloss_get": _tool_get,
    "gloss_related": _tool_related,
    "gloss_recent": _tool_recent,
}


def _rpc_result(msg_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _rpc_error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle_message(msg: dict[str, Any]) -> dict[str, Any] | None:
    """Process one parsed JSON-RPC 2.0 message; return None for notifications."""
    has_id = "id" in msg
    msg_id = msg.get("id")
    method = msg.get("method")
    if not isinstance(method, str) or not method:
        return _rpc_error(msg_id, -32600, "Invalid Request") if has_id else None

    # JSON-RPC notifications have no "id" and expect no response.
    if not has_id:
        return None

    params = msg.get("params")
    if params is None:
        params = {}
    elif not isinstance(params, dict):
        return _rpc_error(msg_id, -32602, "Invalid params: expected object")

    if method == "initialize":
        client_ver = params.get("protocolVersion")
        proto = (
            client_ver
            if isinstance(client_ver, str) and client_ver
            else DEFAULT_PROTOCOL_VERSION
        )
        return _rpc_result(
            msg_id,
            {
                "protocolVersion": proto,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "gloss", "version": __version__},
            },
        )

    if method == "ping":
        return _rpc_result(msg_id, {})

    if method == "tools/list":
        return _rpc_result(msg_id, {"tools": TOOLS})

    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str) or not name:
            return _rpc_error(msg_id, -32602, "Invalid params: missing tool name")
        handler = _TOOL_HANDLERS.get(name)
        if handler is None:
            return _rpc_error(msg_id, -32602, f"Unknown tool: {name}")
        raw_args = params.get("arguments")
        if raw_args is None:
            args: dict[str, Any] = {}
        elif isinstance(raw_args, dict):
            args = raw_args
        else:
            return _rpc_error(msg_id, -32602, "Invalid params: arguments must be an object")
        try:
            payload = handler(args)
        except ValueError as exc:
            return _rpc_result(
                msg_id,
                {"content": [{"type": "text", "text": str(exc)}], "isError": True},
            )
        return _rpc_result(
            msg_id,
            {
                "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
                "isError": False,
            },
        )

    return _rpc_error(msg_id, -32601, f"Method not found: {method}")


def _reconfigure_utf8(stream: Any) -> None:
    reconfig = getattr(stream, "reconfigure", None)
    if callable(reconfig):
        try:
            reconfig(encoding="utf-8")
        except Exception:
            pass


def serve_stdio(in_stream: TextIO | None = None, out_stream: TextIO | None = None) -> None:
    """Run the stdio JSON-RPC 2.0 loop until EOF."""
    inp = sys.stdin if in_stream is None else in_stream
    out = sys.stdout if out_stream is None else out_stream
    if in_stream is None:
        _reconfigure_utf8(inp)
    if out_stream is None:
        _reconfigure_utf8(out)

    for raw_line in inp:
        line = raw_line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            resp: dict[str, Any] | None = _rpc_error(None, -32700, "Parse error")
        else:
            if not isinstance(msg, dict):
                resp = _rpc_error(None, -32600, "Invalid Request")
            else:
                resp = handle_message(msg)
        if resp is not None:
            out.write(json.dumps(resp, ensure_ascii=False) + "\n")
            out.flush()

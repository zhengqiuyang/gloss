"""Local web popup + HTTP API. Binds to 127.0.0.1 only.

Entry points served:
  GET  /            the popup page (pre-filled from the server-side clipboard)
  POST /api/lookup  {"text": str, "note"?, "source"?} -> glossed card JSON

Every future surface (hotkey panel, browser extension, IM bot) should speak
this API instead of importing gloss internals.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional

from . import clipboard, ingest
from .models import Card, GlossError

DEFAULT_PORT = 7789
_MAX_BODY = 1024 * 1024


def card_to_dict(card: Card, linked: Optional[List[Card]] = None,
                 pending: Optional[List[str]] = None) -> Dict[str, Any]:
    linked = linked or []
    pending = pending or []
    return {
        "term": card.term,
        "type": card.type,
        "aliases": card.aliases,
        "one_liner": card.one_liner,
        "detail": card.detail,
        "tags": card.tags,
        "source": card.source,
        "context": card.context,
        "created_at": card.created_at,
        "hits": card.hit_count,
        "linked": [{"term": c.term, "one_liner": c.one_liner} for c in linked],
        "pending": pending,
    }


def result_to_dict(result: Dict[str, Any]) -> Dict[str, Any]:
    card = result["card"]
    payload: Dict[str, Any] = {
        "cache_hit": result.get("cache_hit", False),
        "card": card_to_dict(card, result.get("linked"), result.get("pending")),
    }
    if card.type == "snippet":
        payload["key_terms"] = result.get("key_terms", [])
    return payload


def make_handler():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the popup quiet
            pass

        def _json(self, code: int, payload: Dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path != "/":
                return self._json(404, {"error": "not found"})
            prefill, hint = "", ""
            try:
                kind, data = clipboard.read_clipboard()
                if kind == "text":
                    prefill = data
                else:
                    hint = "剪贴板里是图片，网页版暂不处理图片——用 gloss paste（需配置视觉模型）。"
            except GlossError:
                pass  # empty clipboard: start with an empty input
            html = (
                _PAGE
                .replace("__PREFILL__", json.dumps(prefill, ensure_ascii=False).replace("</", "<\\/"))
                .replace("__HINT__", json.dumps(hint, ensure_ascii=False).replace("</", "<\\/"))
            )
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if self.path != "/api/lookup":
                return self._json(404, {"error": "not found"})
            try:
                length = min(int(self.headers.get("Content-Length", 0)), _MAX_BODY)
                data = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError):
                return self._json(400, {"error": "请求体不是合法 JSON"})
            text = str(data.get("text") or "").strip()
            if not text:
                return self._json(400, {"error": "text 不能为空"})
            try:
                if len(text) <= ingest.TERM_MAX_CHARS and "\n" not in text:
                    result = ingest.lookup_or_ingest(
                        text, note=str(data.get("note") or ""), source=str(data.get("source") or "")
                    )
                else:
                    result = ingest.ingest_piped(text)
            except GlossError as exc:
                return self._json(400, {"error": str(exc)})
            except Exception as exc:  # never leak a traceback into the popup
                return self._json(500, {"error": f"内部错误: {exc}"})
            self._json(200, result_to_dict(result))

    return Handler


def start_server(port: int = DEFAULT_PORT) -> HTTPServer:
    """Bind 127.0.0.1 only; pass port 0 in tests to pick a free one."""
    return HTTPServer(("127.0.0.1", port), make_handler())


_PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>gloss</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 28px 20px 40px;
    font: 15px/1.65 -apple-system, "PingFang SC", "Segoe UI", sans-serif;
    background: light-dark(#fafafa, #1c1c1e); color: light-dark(#1d1d1f, #f2f2f7);
    display: flex; justify-content: center;
  }
  main { width: 100%; max-width: 620px; }
  #q {
    width: 100%; padding: 14px 16px; font-size: 17px;
    border-radius: 12px; border: 1px solid light-dark(#d2d2d7, #3a3a3c);
    background: light-dark(#fff, #2c2c2e); color: inherit; outline: none;
  }
  #q:focus { border-color: #0a84ff; }
  .hint { color: #ff9f0a; font-size: 13px; margin-top: 8px; min-height: 1em; }
  .badge { display: inline-block; font-size: 12px; padding: 3px 10px; border-radius: 999px; margin-bottom: 10px; }
  .badge.new { background: rgba(10,132,255,.14); color: #0a84ff; }
  .badge.hit { background: rgba(48,209,88,.16); color: #30d158; }
  .card {
    margin-top: 20px; padding: 20px 22px; border-radius: 14px;
    background: light-dark(#fff, #2c2c2e);
    border: 1px solid light-dark(#e5e5ea, #3a3a3c);
  }
  h1 { font-size: 22px; margin: 0 0 2px; }
  .aka { color: #8e8e93; font-size: 13px; }
  .oneliner { font-weight: 600; margin: 12px 0 8px; }
  .detail { white-space: pre-wrap; }
  .tags { margin-top: 12px; }
  .tag { display: inline-block; font-size: 12px; color: #bf5af2; background: rgba(191,90,242,.12); border-radius: 6px; padding: 2px 8px; margin-right: 6px; }
  .rel { margin-top: 14px; border-top: 1px solid light-dark(#e5e5ea, #3a3a3c); padding-top: 12px; }
  .rel a { cursor: pointer; }
  .rel .ln { display: block; padding: 2px 0; color: #0a84ff; }
  .rel .ln small { color: #8e8e93; margin-left: 6px; }
  .rel .pd { display: block; padding: 2px 0; color: #ff9f0a; }
  .meta { margin-top: 14px; color: #8e8e93; font-size: 12px; }
  .kts { margin-top: 14px; }
  .kt { display: inline-block; cursor: pointer; font-size: 13px; padding: 4px 12px; margin: 0 6px 6px 0; border-radius: 999px; background: rgba(255,159,10,.14); color: #ff9f0a; }
  .err { margin-top: 20px; padding: 14px 18px; border-radius: 12px; background: rgba(255,69,58,.12); color: #ff453a; white-space: pre-wrap; }
  .spin { color: #8e8e93; margin-top: 20px; }
  kbd { font: 12px ui-monospace, monospace; background: light-dark(#e5e5ea, #3a3a3c); border-radius: 4px; padding: 1px 6px; }
  .foot { color: #8e8e93; font-size: 12px; margin-top: 26px; text-align: center; }
</style>
</head>
<body>
<main>
  <input id="q" type="text" placeholder="丢一个词进来，回车查询 / 收录" autocomplete="off" spellcheck="false" autofocus>
  <div class="hint" id="hint"></div>
  <div id="out"><div class="foot">输入词或一段话，回车。<kbd>Esc</kbd> 关窗 · 点击任何关联词可继续查询</div></div>
</main>
<script>
const PREFILL = __PREFILL__;
const HINT = __HINT__;
const q = document.getElementById("q");
const out = document.getElementById("out");
const hintEl = document.getElementById("hint");
hintEl.textContent = HINT;
q.value = PREFILL;
q.focus();
q.setSelectionRange(0, q.value.length);

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}
function glossNow(term) { q.value = term; lookup(); }

function render(d) {
  out.textContent = "";
  if (d.error) { out.appendChild(el("div", "err", d.error)); return; }
  const c = d.card;
  const card = el("div", "card");
  card.appendChild(el("span", "badge " + (d.cache_hit ? "hit" : "new"),
    d.cache_hit ? "⚡ 缓存命中 · 0 tokens" : "✨ 已收录新卡片"));
  card.appendChild(el("h1", null, c.term));
  if (c.aliases && c.aliases.length) card.appendChild(el("div", "aka", "aka " + c.aliases.join(" / ")));
  if (c.one_liner) card.appendChild(el("div", "oneliner", c.one_liner));
  if (c.detail) card.appendChild(el("div", "detail", c.detail));
  if (c.tags && c.tags.length) {
    const t = el("div", "tags");
    c.tags.forEach(x => t.appendChild(el("span", "tag", "#" + x)));
    card.appendChild(t);
  }
  if ((c.linked && c.linked.length) || (c.pending && c.pending.length)) {
    const r = el("div", "rel");
    (c.linked || []).forEach(x => {
      const a = el("a", "ln", "🔗 " + x.term);
      const s = el("small", null, "已关联" + (x.one_liner ? " · " + x.one_liner : ""));
      a.appendChild(s);
      a.onclick = () => glossNow(x.term);
      r.appendChild(a);
    });
    (c.pending || []).forEach(x => {
      const a = el("a", "pd", "· " + x + "（未收录，点一下就查）");
      a.onclick = () => glossNow(x);
      r.appendChild(a);
    });
    card.appendChild(r);
  }
  if (c.source || c.context) {
    const bits = [];
    if (c.created_at) bits.push("首次记录 " + c.created_at.slice(0, 10));
    if (c.source) bits.push("来源 " + c.source);
    if (c.context) bits.push("语境 " + c.context);
    card.appendChild(el("div", "meta", bits.join(" · ")));
  }
  out.appendChild(card);
  if (d.key_terms && d.key_terms.length) {
    const box = el("div", "card");
    box.appendChild(el("div", "aka", "值得单独收录的词（点一下就查）："));
    const wrap = el("div", "kts");
    d.key_terms.forEach(x => {
      const b = el("span", "kt", x);
      b.onclick = () => glossNow(x);
      wrap.appendChild(b);
    });
    box.appendChild(wrap);
    out.appendChild(box);
  }
}

async function lookup() {
  const text = q.value.trim();
  if (!text) return;
  out.textContent = "";
  out.appendChild(el("div", "spin", "查询中…"));
  try {
    const r = await fetch("/api/lookup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text })
    });
    render(await r.json());
  } catch (e) {
    render({ error: "请求失败: " + e });
  }
}
q.addEventListener("keydown", e => { if (e.key === "Enter") lookup(); });
document.addEventListener("keydown", e => { if (e.key === "Escape") window.close(); });
</script>
</body>
</html>
"""

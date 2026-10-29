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
from .config import load_config
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
            from urllib.parse import parse_qs, urlparse

            route = urlparse(self.path)
            if route.path != "/":
                return self._json(404, {"error": "not found"})
            glass = "1" in parse_qs(route.query).get("glass", [])
            prefill, hint, clip_kind = "", "", "none"
            try:
                kind, data = clipboard.read_clipboard()
                clip_kind = kind
                if kind == "text":
                    prefill = data
            except GlossError:
                pass  # empty clipboard: start with an empty input
            html = (
                _PAGE
                .replace("__PREFILL__", json.dumps(prefill, ensure_ascii=False).replace("</", "<\\/"))
                .replace("__HINT__", json.dumps(hint, ensure_ascii=False).replace("</", "<\\/"))
                .replace("__CLIP_KIND__", json.dumps(clip_kind))
                .replace("<body>", '<body class="glass">' if glass else "<body>")
            )
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if self.path == "/api/image":
                return self._image()
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

        def _image(self) -> None:
            """Digest whatever image is currently in the clipboard."""
            if not self._vision_configured():
                from .clipboard import image_supported_hint

                return self._json(400, {"error": image_supported_hint()})
            try:
                kind, path = clipboard.read_clipboard()
            except GlossError as exc:
                return self._json(400, {"error": str(exc)})
            if kind != "image":
                return self._json(400, {"error": "剪贴板里没有图片。先截图（⌘⇧⌃4 存剪贴板）再触发。"})
            try:
                result = ingest.ingest_image(path, note="")
            except GlossError as exc:
                return self._json(400, {"error": str(exc)})
            except Exception as exc:
                return self._json(500, {"error": f"内部错误: {exc}"})
            self._json(200, result_to_dict(result))

        @staticmethod
        def _vision_configured() -> bool:
            cfg = load_config()
            llm = cfg["llm"]
            return bool(llm.get("vision_model") or llm.get("model"))

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
  :root {
    color-scheme: light dark;
    --text: light-dark(#1d1d1f, #f2f2f5);
    --text2: light-dark(#6f6f76, #a3a3ab);
    --text3: light-dark(#3a3a41, #d6d6db);
    --accent: #0a84ff;
    --amber: #ff9f0a;
    --purple: #bf5af2;
    --green: #30d158;
    --stroke: light-dark(rgba(255,255,255,.65), rgba(255,255,255,.10));
    --fill: light-dark(rgba(255,255,255,.60), rgba(32,32,38,.46));
  }
  * { box-sizing: border-box; margin: 0; }
  html { height: 100%; background: transparent; }
  body {
    height: 100%;
    font: 13.5px/1.6 -apple-system, "PingFang SC", "Helvetica Neue", sans-serif;
    color: var(--text);
    -webkit-font-smoothing: antialiased;
    background:
      radial-gradient(820px 460px at -8% -12%, rgba(10,132,255,.22), transparent 62%),
      radial-gradient(640px 400px at 108% -4%, rgba(191,90,242,.17), transparent 60%),
      radial-gradient(680px 460px at 50% 118%, rgba(255,159,10,.12), transparent 62%),
      light-dark(#eceef5, #141418);
  }
  /* app 弹窗（?glass=1）：底层渐变加一点透明度，若 WebView 透明生效则还能透出桌面模糊 */
  body.glass {
    background:
      radial-gradient(820px 460px at -8% -12%, rgba(10,132,255,.20), transparent 62%),
      radial-gradient(640px 400px at 108% -4%, rgba(191,90,242,.15), transparent 60%),
      radial-gradient(680px 460px at 50% 118%, rgba(255,159,10,.10), transparent 62%),
      light-dark(rgba(236,238,245,.88), rgba(20,20,24,.86));
  }
  .app { max-width: 720px; margin: 0 auto; padding: 12px 12px 16px; }

  .searchbar {
    display: flex; align-items: center; gap: 8px;
    background: var(--fill);
    -webkit-backdrop-filter: blur(24px) saturate(1.6);
    backdrop-filter: blur(24px) saturate(1.6);
    border: 1px solid var(--stroke);
    border-radius: 11px; padding: 8px 12px;
    box-shadow: 0 4px 18px rgba(0,0,0,.07);
    position: sticky; top: 10px; z-index: 10;
    transition: border-color .16s, box-shadow .16s;
  }
  .searchbar:focus-within {
    border-color: rgba(10,132,255,.65);
    box-shadow: 0 0 0 3px rgba(10,132,255,.15), 0 4px 18px rgba(0,0,0,.07);
  }
  .searchbar .icon { font-size: 15px; user-select: none; }
  #q {
    flex: 1; border: 0; outline: 0; background: transparent; color: inherit;
    font-size: 14px; font-family: inherit;
  }
  #q::placeholder { color: var(--text2); }
  kbd {
    font: 10.5px ui-monospace, SFMono-Regular, monospace;
    color: var(--text2);
    border: 1px solid light-dark(rgba(0,0,0,.14), rgba(255,255,255,.16));
    border-bottom-width: 2px; border-radius: 5px; padding: 1px 6px; user-select: none;
  }
  .hint { color: var(--amber); font-size: 12px; margin: 7px 4px 0; min-height: 1.2em; }

  #out { margin-top: 10px; max-height: calc(100vh - 104px); overflow-y: auto;
         scrollbar-width: thin; }
  #out::-webkit-scrollbar { width: 8px; }
  #out::-webkit-scrollbar-thumb { background: rgba(128,128,128,.35); border-radius: 4px; }

  .card {
    background: var(--fill);
    -webkit-backdrop-filter: blur(24px) saturate(1.6);
    backdrop-filter: blur(24px) saturate(1.6);
    border: 1px solid var(--stroke);
    border-radius: 12px; padding: 13px 15px 12px; margin-bottom: 9px;
    box-shadow: 0 8px 22px rgba(0,0,0,.08);
    animation: pop .24s cubic-bezier(.2, .9, .3, 1.15);
  }
  @keyframes pop { from { opacity: 0; transform: translateY(10px) scale(.985); } }

  .topline { display: flex; align-items: center; gap: 9px; margin-bottom: 8px; }
  .badge {
    font-size: 10.5px; font-weight: 600; letter-spacing: .02em;
    padding: 2px 9px; border-radius: 999px;
  }
  .badge.new { color: var(--accent); background: rgba(10,132,255,.14); }
  .badge.hit { color: var(--green); background: rgba(48,209,88,.15); }
  .hits { font-size: 11px; color: var(--text2); }

  h1 { font-size: 18px; font-weight: 800; letter-spacing: -.02em; line-height: 1.25; }
  .aka { color: var(--text2); font-size: 11.5px; margin-top: 3px; }

  .oneliner {
    margin: 10px 0 8px; padding: 6px 10px;
    font-weight: 600; font-size: 13px; line-height: 1.55;
    border-left: 3px solid var(--accent); border-radius: 8px;
    background: rgba(10,132,255,.08);
  }
  .detail { font-size: 12.5px; color: var(--text3); white-space: pre-wrap; }

  .tags { margin-top: 10px; display: flex; flex-wrap: wrap; gap: 6px; }
  .tag {
    font-size: 10.5px; font-weight: 500; color: var(--purple);
    background: rgba(191,90,242,.12); padding: 2px 8px; border-radius: 999px;
  }

  .section { margin-top: 11px; border-top: 1px solid light-dark(rgba(0,0,0,.07), rgba(255,255,255,.08)); padding-top: 9px; }
  .label {
    font-size: 10.5px; font-weight: 700; color: var(--text2);
    text-transform: uppercase; letter-spacing: .07em; margin-bottom: 5px;
  }
  .ln, .pd {
    display: flex; align-items: baseline; gap: 8px;
    padding: 4px 8px; margin: 0 -8px; border-radius: 8px;
    cursor: pointer; text-decoration: none;
  }
  .ln:hover, .pd:hover { background: rgba(10,132,255,.09); }
  .ln .t { color: var(--accent); font-weight: 600; }
  .pd .t { color: var(--amber); font-weight: 600; }
  .ln small, .pd small { color: var(--text2); font-size: 11px; }
  .meta { margin-top: 12px; color: var(--text2); font-size: 10.5px; }

  .kts { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 7px; }
  .kt {
    display: inline-block; cursor: pointer; font-size: 12px; font-weight: 500;
    padding: 4px 12px; border-radius: 999px;
    background: rgba(255,159,10,.13); color: var(--amber);
    transition: transform .1s ease, background .1s ease;
  }
  .kt:hover { transform: scale(1.05); background: rgba(255,159,10,.22); }

  .err {
    background: rgba(255,69,58,.12); color: #ff453a;
    border-radius: 12px; padding: 12px 16px; font-size: 13px;
    white-space: pre-wrap; animation: pop .2s ease;
  }
  .spin {
    color: var(--text2); text-align: center; padding: 22px 0; font-size: 13px;
    animation: pulse 1.1s ease-in-out infinite;
  }
  @keyframes pulse { 50% { opacity: .45; } }

  .empty { text-align: center; color: var(--text2); padding: 42px 0 22px; }
  .empty .big { font-size: 28px; margin-bottom: 8px; }
  .empty .slogan { font-size: 13px; color: var(--text3); margin-bottom: 10px; }
  .empty .tips { font-size: 11.5px; line-height: 2; }
</style>
</head>
<body>
<div class="app">
  <div class="searchbar">
    <span class="icon">📖</span>
    <input id="q" type="text" placeholder="丢一个词进来，回车查询 / 收录" autocomplete="off" spellcheck="false" autofocus>
    <kbd>↵</kbd>
  </div>
  <div class="hint" id="hint"></div>
  <div id="out">
    <div class="empty">
      <div class="big">📖</div>
      <div class="slogan">查一次，永久拥有。</div>
      <div class="tips">复制后呼出即自动查询 · <kbd>Esc</kbd> 关窗 · 点击任何关联词继续查</div>
    </div>
  </div>
</div>
<script>
const PREFILL = __PREFILL__;
const HINT = __HINT__;
const CLIP_KIND = __CLIP_KIND__;
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
  const top = el("div", "topline");
  const badgeText = d.cache_hit ? "⚡ 缓存命中 · 0 token"
                  : (c.type === "snippet" ? "✨ 已收藏摘要" : "✨ 已收录新术语");
  top.appendChild(el("span", "badge " + (d.cache_hit ? "hit" : "new"), badgeText));
  if (d.cache_hit && c.hits > 0) top.appendChild(el("span", "hits", "第 " + c.hits + " 次查询"));
  card.appendChild(top);

  card.appendChild(el("h1", null, c.term));
  if (c.aliases && c.aliases.length)
    card.appendChild(el("div", "aka", "又称 " + c.aliases.join(" / ")));
  if (c.one_liner) card.appendChild(el("div", "oneliner", c.one_liner));
  if (c.detail) card.appendChild(el("div", "detail", c.detail));

  if (c.tags && c.tags.length) {
    const t = el("div", "tags");
    c.tags.forEach(x => t.appendChild(el("span", "tag", "#" + x)));
    card.appendChild(t);
  }

  if ((c.linked && c.linked.length) || (c.pending && c.pending.length)) {
    const sec = el("div", "section");
    sec.appendChild(el("div", "label", "关联概念"));
    (c.linked || []).forEach(x => {
      const a = el("a", "ln");
      a.appendChild(el("span", "t", "🔗 " + x.term));
      if (x.one_liner) a.appendChild(el("small", null, x.one_liner));
      a.onclick = () => glossNow(x.term);
      sec.appendChild(a);
    });
    (c.pending || []).forEach(x => {
      const a = el("a", "pd");
      a.appendChild(el("span", "t", "· " + x));
      a.appendChild(el("small", null, "未收录，点一下就查"));
      a.onclick = () => glossNow(x);
      sec.appendChild(a);
    });
    card.appendChild(sec);
  }

  const bits = [];
  if (c.created_at) bits.push("首次记录 " + c.created_at.slice(0, 10));
  if (c.source) bits.push("来源 " + c.source);
  if (c.context) bits.push("语境 " + c.context);
  if (bits.length) card.appendChild(el("div", "meta", bits.join(" · ")));

  out.appendChild(card);

  if (d.key_terms && d.key_terms.length) {
    const box = el("div", "card");
    const sec = el("div", "section");
    sec.style.borderTop = "none";
    sec.style.paddingTop = "0";
    sec.appendChild(el("div", "label", "值得单独收录的词"));
    const kts = el("div", "kts");
    d.key_terms.forEach(x => {
      const b = el("span", "kt", x);
      b.onclick = () => glossNow(x);
      kts.appendChild(b);
    });
    sec.appendChild(kts);
    box.appendChild(sec);
    out.appendChild(box);
  }
  out.scrollTop = 0;
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

async function lookupImage() {
  out.textContent = "";
  out.appendChild(el("div", "spin", "解析剪贴板里的图片…"));
  try {
    const r = await fetch("/api/image", { method: "POST" });
    render(await r.json());
  } catch (e) {
    render({ error: "请求失败: " + e });
  }
}
q.addEventListener("keydown", e => { if (e.key === "Enter") lookup(); });
document.addEventListener("keydown", e => { if (e.key === "Escape") window.close(); });

// 热键/打开即意图：剪贴板里有词就直接查，有图就直接解析，不再多按一次回车
if (CLIP_KIND === "image") lookupImage();
else if (PREFILL.trim()) lookup();
</script>
</body>
</html>
"""

"""Local web popup + HTTP API. Binds to 127.0.0.1 only.

Entry points served:
  GET  /                 the popup page (pre-filled from the server-side clipboard)
  POST /api/lookup       {"text": str, "note"?, "source"?} -> glossed card JSON
  POST /api/image        digest whatever image is in the clipboard
  POST /api/translate    {"text": str} -> cached translation
  GET  /api/config       current settings (api key masked)
  POST /api/config       save settings ({"model": ..., "api_key": ...})
  POST /api/config/test  one tiny real completion; classifies the failure
  POST /api/demo         seed the offline demo glossary (no API key needed)

Every future surface (hotkey panel, browser extension, IM bot) should speak
this API instead of importing gloss internals.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional

from . import clipboard, ingest
from .config import load_config, resolve_api_key
from .models import Card, GlossError

DEFAULT_PORT = 7789
_MAX_BODY = 1024 * 1024

# settings-page names -> config.toml dotted keys
_SETTING_ROUTES = {
    "base_url": "llm.base_url",
    "model": "llm.model",
    "vision_model": "llm.vision_model",
    "api_key": "llm.api_key",
    "hotkey": "app.hotkey",
    "auto_lookup": "app.auto_lookup",
    "language": "glossary.language",
}


def settings_payload() -> Dict[str, Any]:
    """What the settings page is allowed to see; the key is always masked."""
    cfg = load_config()
    llm, app_, g = cfg["llm"], cfg["app"], cfg["glossary"]
    key = llm.get("api_key", "")
    masked = (key[:4] + "…" + key[-4:]) if len(key) > 8 else ("…" if key else "")
    return {
        "base_url": llm["base_url"],
        "model": llm["model"],
        "vision_model": llm.get("vision_model", ""),
        "api_key": masked,
        "hotkey": app_["hotkey"],
        "language": g["language"],
        "auto_lookup": app_.get("auto_lookup", True),
        "has_api_key": resolve_api_key(cfg) is not None,
    }


def classify_llm_error(message: str) -> str:
    """Map a raw provider error onto the four failure modes users actually hit."""
    m = message.lower()
    if "401" in m or "unauthorized" in m or "invalid api key" in m or "authentication" in m:
        return "认证失败：API key 不对或已过期。"
    if "404" in m or "model_not_found" in m or "does not exist" in m or "no such model" in m:
        return "模型不存在：检查「文字模型」的拼写，或该服务是否提供这个模型。"
    if "timed out" in m or "timeout" in m or "超时" in m:
        return "连接超时：服务地址可达但响应太慢，或网络受限。"
    if "connection" in m or "resolve" in m or "getaddrinfo" in m or "refused" in m or "unreachable" in m:
        return "连不上服务地址：检查「服务地址」拼写（OpenAI 兼容服务通常以 /v1 结尾）。"
    return "测试失败：" + message[:200]


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
            if route.path == "/api/config":
                return self._json(200, settings_payload())
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
            cfg = load_config()
            html = (
                _PAGE
                .replace("__PREFILL__", json.dumps(prefill, ensure_ascii=False).replace("</", "<\\/"))
                .replace("__HINT__", json.dumps(hint, ensure_ascii=False).replace("</", "<\\/"))
                .replace("__CLIP_KIND__", json.dumps(clip_kind))
                .replace("__AUTO_LOOKUP__", json.dumps(bool(cfg["app"].get("auto_lookup", True))))
                .replace("__NO_KEY__", json.dumps(resolve_api_key(cfg) is None))
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
            if self.path == "/api/translate":
                return self._translate()
            if self.path == "/api/config":
                return self._config_set()
            if self.path == "/api/config/test":
                return self._config_test()
            if self.path == "/api/demo":
                return self._demo()
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

        def _translate(self) -> None:
            try:
                length = min(int(self.headers.get("Content-Length", 0)), _MAX_BODY)
                data = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError):
                return self._json(400, {"error": "请求体不是合法 JSON"})
            text = str(data.get("text") or "").strip()
            if not text:
                return self._json(400, {"error": "text 不能为空"})
            try:
                result = ingest.translate_text(text)
            except GlossError as exc:
                return self._json(400, {"error": str(exc)})
            except Exception as exc:
                return self._json(500, {"error": f"内部错误: {exc}"})
            self._json(200, result)

        @staticmethod
        def _vision_configured() -> bool:
            cfg = load_config()
            llm = cfg["llm"]
            return bool(llm.get("vision_model") or llm.get("model"))

        def _read_body(self) -> Dict[str, Any]:
            length = min(int(self.headers.get("Content-Length", 0)), _MAX_BODY)
            return json.loads(self.rfile.read(length) or b"{}")

        def _config_set(self) -> None:
            from .config import set_config_value

            try:
                data = self._read_body()
            except (ValueError, json.JSONDecodeError):
                return self._json(400, {"error": "请求体不是合法 JSON"})
            if not isinstance(data, dict) or not data:
                return self._json(400, {"error": "没有要保存的设置"})
            try:
                for key, value in data.items():
                    dotted = _SETTING_ROUTES.get(key)
                    if dotted is None:
                        return self._json(400, {"error": f"不认识的设置项: {key}"})
                    literal = str(value).lower() if isinstance(value, bool) else str(value)
                    set_config_value(dotted, literal)
            except GlossError as exc:
                return self._json(400, {"error": str(exc)})
            return self._json(200, {"ok": True, "saved": list(data.keys()),
                                    "config": settings_payload()})

        def _config_test(self) -> None:
            cfg = load_config()
            if resolve_api_key(cfg) is None:
                return self._json(200, {
                    "ok": False,
                    "error": "还没有配置 API key——在设置里填好并保存后再测试。",
                })
            try:
                from .llm import GlossLLM

                llm = GlossLLM(cfg)
                client = llm.client  # raises the friendly no-key error before any network
                resp = client.chat.completions.create(
                    model=cfg["llm"]["model"],
                    messages=[{"role": "user", "content": "Reply with the single word: ok"}],
                    max_tokens=4,
                )
                reply = (resp.choices[0].message.content or "").strip()
                return self._json(200, {"ok": True, "reply": reply[:40] or "（空响应）"})
            except Exception as exc:
                return self._json(200, {"ok": False, "error": classify_llm_error(str(exc))})

        def _demo(self) -> None:
            from .demo import seed_demo

            added, skipped = seed_demo()
            self._json(200, {"added": added, "skipped": skipped})

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
  body.glass .app { padding-top: 20px; }  /* 顶部留出原生拖动带 */
  body.glass .card { box-shadow: none; }
  body.glass .searchbar { box-shadow: none; }

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
  .tbtn {
    font-size: 12px; font-weight: 600; cursor: pointer;
    color: var(--accent); background: rgba(10,132,255,.10);
    border: 1px solid rgba(10,132,255,.35); border-radius: 8px;
    padding: 3px 11px; user-select: none; transition: background .12s;
  }
  .tbtn:hover { background: rgba(10,132,255,.22); }
  .hint { color: var(--amber); font-size: 12px; margin: 7px 4px 0; min-height: 1.2em; }

  /* settings panel */
  .frow { display: flex; align-items: center; gap: 8px; margin: 7px 0; }
  .frow label { width: 72px; flex: none; color: var(--text2); font-size: 12px; }
  .frow input[type="text"], .frow input[type="password"] {
    flex: 1; min-width: 0;
    border: 1px solid var(--stroke); border-radius: 8px; padding: 5px 10px;
    background: transparent; color: inherit; font-family: inherit; font-size: 12.5px;
  }
  .frow input:focus { outline: 0; border-color: rgba(10,132,255,.65); }
  .frow small { color: var(--text2); font-size: 11px; }
  .sbtn {
    display: inline-block; cursor: pointer; font-size: 12px; font-weight: 600;
    padding: 5px 14px; border-radius: 999px;
    background: rgba(10,132,255,.13); color: var(--accent);
  }
  .sbtn:hover { background: rgba(10,132,255,.24); }
  .sbtn.warm { background: rgba(255,159,10,.13); color: var(--amber); }

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
    <button id="trbtn" class="tbtn" title="翻译输入框里的内容（译文缓存复用）">译</button>
    <button id="cfgbtn" class="tbtn" title="设置：服务 / 模型 / API key / 快捷键">⚙</button>
  </div>
  <div class="hint" id="hint"></div>
  <div id="out">
    <div class="empty">
      <div class="big">📖</div>
      <div class="slogan">查一次，永久拥有。</div>
      <div class="tips">复制后呼出即查询（可在设置改为回车确认） · <kbd>Esc</kbd> 关窗 · 点关联词继续查</div>
    </div>
  </div>
  <div id="settings" class="card" style="display:none">
    <div class="topline"><span class="badge new">⚙ 设置</span></div>
    <div id="sbody"></div>
    <div class="meta" style="margin-top:9px">视觉模型与文字模型共用服务地址和 API key · 热键改动需重启 gloss app 生效 · API key 明文保存在 ~/.gloss/config.toml（后续会支持 macOS Keychain）</div>
    <div class="kts" style="margin-top:8px">
      <span class="sbtn" id="ssave">保存</span>
      <span class="sbtn warm" id="stest">测试连接（会真实调用一次模型）</span>
      <span class="sbtn" id="sclose" style="background:rgba(128,128,128,.14);color:var(--text2)">关闭</span>
    </div>
    <div class="hint" id="smsg"></div>
  </div>
</div>
<script>
const PREFILL = __PREFILL__;
const HINT = __HINT__;
const CLIP_KIND = __CLIP_KIND__;
const AUTO = __AUTO_LOOKUP__;
let NO_KEY = __NO_KEY__;
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

let busy = false;  // 同一时刻只放行一个请求，避免重复触发同一查询

async function lookup() {
  if (busy) return;
  const text = q.value.trim();
  if (!text) return;
  busy = true;
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
  } finally {
    busy = false;
  }
}

async function lookupImage() {
  if (busy) return;
  busy = true;
  out.textContent = "";
  out.appendChild(el("div", "spin", "解析剪贴板里的图片…"));
  try {
    const r = await fetch("/api/image", { method: "POST" });
    render(await r.json());
  } catch (e) {
    render({ error: "请求失败: " + e });
  } finally {
    busy = false;
  }
}
function renderTranslation(d) {
  out.textContent = "";
  if (d.error) { out.appendChild(el("div", "err", d.error)); return; }
  const card = el("div", "card");
  const top = el("div", "topline");
  top.appendChild(el("span", "badge " + (d.cache_hit ? "hit" : "new"),
    d.cache_hit ? "⚡ 缓存命中 · 0 token · 第 " + d.hits + " 次" : "🌐 已翻译 · 已缓存"));
  card.appendChild(top);
  const src = el("div", "oneliner", d.source);
  src.style.borderLeftColor = "var(--amber)";
  src.style.background = "rgba(255,159,10,.08)";
  src.style.color = "var(--text2)";
  src.style.fontWeight = "400";
  card.appendChild(src);
  card.appendChild(el("div", "detail", d.translation));
  card.appendChild(el("div", "meta", "目标语言 " + d.target + " · 相同内容再次翻译不花 token"));
  out.appendChild(card);
  out.scrollTop = 0;
}

async function translateCurrent() {
  if (busy) return;
  const text = q.value.trim();
  if (!text) return;
  busy = true;
  out.textContent = "";
  out.appendChild(el("div", "spin", "翻译中…"));
  try {
    const r = await fetch("/api/translate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text })
    });
    renderTranslation(await r.json());
  } catch (e) {
    renderTranslation({ error: "请求失败: " + e });
  } finally {
    busy = false;
  }
}
document.getElementById("trbtn").addEventListener("click", translateCurrent);

q.addEventListener("keydown", e => {
  if (e.key !== "Enter") return;
  // 手动确认模式下，剪贴板是截图而输入框为空：回车 = 确认解析截图
  if (!q.value.trim() && CLIP_KIND === "image") { lookupImage(); return; }
  lookup();
});
document.addEventListener("keydown", e => { if (e.key === "Escape") window.close(); });

// -- 首次引导 / 两种查询模式 ------------------------------------------------
// AUTO=true: 呼出即查询（会把剪贴板未命中内容发给模型服务商）
// AUTO=false: 只预填，按回车确认（文字与截图都覆盖）

function renderOnboarding() {
  out.textContent = "";
  const card = el("div", "card");
  const top = el("div", "topline");
  top.appendChild(el("span", "badge new", "👋 第一次用"));
  card.appendChild(top);
  card.appendChild(el("h1", null, "先配一个模型服务，或先白嫖示例"));
  card.appendChild(el("div", "detail",
    "收录新词需要一个 OpenAI 兼容服务（DeepSeek / GLM / Ollama 本地…都行）。"
    + "不配 key 也能先逛：导入 9 张互相关联的示例卡，浏览、搜索、关联全部离线可用。"));
  const row = el("div", "kts");
  const demoBtn = el("span", "kt", "📚 导入示例词库（离线，不需要 key）");
  demoBtn.onclick = importDemo;
  const cfgBtn = el("span", "kt", "⚙ 打开设置");
  cfgBtn.onclick = () => toggleSettings();
  row.appendChild(demoBtn);
  row.appendChild(cfgBtn);
  card.appendChild(row);
  card.appendChild(el("div", "meta",
    "自动查询模式下，呼出弹窗会把剪贴板里的内容发给模型服务商；"
    + "想每次手动确认，可在设置里关掉「自动查询」。"));
  out.appendChild(card);
  out.scrollTop = 0;
}

async function importDemo() {
  if (busy) return;
  busy = true;
  out.textContent = "";
  out.appendChild(el("div", "spin", "导入示例中…"));
  let seeded = null;
  try {
    const r = await fetch("/api/demo", { method: "POST" });
    seeded = await r.json();
    if (seeded.error) { render({ error: seeded.error }); return; }
    hintEl.textContent = "已导入 " + seeded.added + " 张示例卡"
      + (seeded.skipped ? "（跳过 " + seeded.skipped + " 张你已有的）" : "")
      + "。输入一个词试试，或点下面这张卡里的关联词。";
  } catch (e) {
    render({ error: "请求失败: " + e });
    return;
  } finally {
    busy = false;  // 先释放请求锁，后面的 glossNow 才能真正发起查询
  }
  glossNow("RAG");  // 渲染真实导入的 RAG 卡（本地命中，0 token）
}

if (NO_KEY) {
  renderOnboarding();  // 还没有 key：引导而不是白白发一次会失败的请求
} else if (CLIP_KIND === "image") {
  if (AUTO) lookupImage();
  else hintEl.textContent = "剪贴板里有截图——按回车解析（当前为手动确认模式）。";
} else if (PREFILL.trim()) {
  if (AUTO) lookup();
  else hintEl.textContent = "已预填剪贴板内容——按回车确认查询（当前为手动确认模式）。";
}

// -- 设置面板 ---------------------------------------------------------------

const settingsEl = document.getElementById("settings");
const sbody = document.getElementById("sbody");
const smsg = document.getElementById("smsg");
document.getElementById("cfgbtn").addEventListener("click", toggleSettings);
document.getElementById("sclose").addEventListener("click", () => { settingsEl.style.display = "none"; });

async function toggleSettings() {
  if (settingsEl.style.display === "none") {
    await loadSettings();
    settingsEl.style.display = "";
  } else {
    settingsEl.style.display = "none";
  }
}

function fieldRow(key, label, current, placeholder) {
  const row = el("div", "frow");
  row.appendChild(el("label", null, label));
  const input = document.createElement("input");
  input.type = key === "api_key" ? "password" : "text";
  input.id = "set-" + key;
  // 预填真实值：改动（包括清空）才会提交；API key 例外，留空 = 不改动
  input.value = key === "api_key" ? "" : (current || "");
  input.dataset.pristine = current || "";
  input.placeholder = placeholder || "";
  input.autocomplete = "off";
  input.spellcheck = false;
  row.appendChild(input);
  return row;
}

async function loadSettings() {
  smsg.textContent = "";
  try {
    const d = await (await fetch("/api/config")).json();
    sbody.textContent = "";
    sbody.appendChild(fieldRow("base_url", "服务地址", d.base_url));
    sbody.appendChild(fieldRow("model", "文字模型", d.model));
    sbody.appendChild(fieldRow("vision_model", "视觉模型", d.vision_model, "留空则与文字模型共用"));
    sbody.appendChild(fieldRow("api_key", "API key", "", d.api_key || "sk-…（已保存的会打码）"));
    sbody.appendChild(fieldRow("hotkey", "全局热键", d.hotkey));
    sbody.appendChild(fieldRow("language", "解释语言", d.language));
    const autoRow = el("div", "frow");
    autoRow.appendChild(el("label", null, "自动查询"));
    const check = document.createElement("input");
    check.type = "checkbox";
    check.id = "set-auto_lookup";
    check.checked = !!d.auto_lookup;
    autoRow.appendChild(check);
    autoRow.appendChild(el("small", null, "呼出弹窗即查询剪贴板；关掉则预填，等回车确认"));
    sbody.appendChild(autoRow);
  } catch (e) {
    smsg.textContent = "读取设置失败: " + e;
  }
}

document.getElementById("ssave").addEventListener("click", async () => {
  const payload = { auto_lookup: document.getElementById("set-auto_lookup").checked };
  let blocked = "";
  ["base_url", "model", "vision_model", "hotkey", "language"].forEach(k => {
    const input = document.getElementById("set-" + k);
    const now = input.value.trim();
    const before = (input.dataset.pristine || "").trim();
    if (now === before) return;  // 没改就不提交
    if (!now && (k === "base_url" || k === "model")) blocked = "服务地址和文字模型不能清空";
    payload[k] = now;            // 改过就提交——清空也是一次修改
  });
  if (blocked) { smsg.textContent = "✗ " + blocked; return; }
  const key = document.getElementById("set-api_key").value.trim();
  if (key) payload.api_key = key;  // API key 留空 = 不改动已保存的
  try {
    const d = await (await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    })).json();
    if (d.error) { smsg.textContent = "✗ " + d.error; return; }
    NO_KEY = false;
    smsg.textContent = "✓ 已保存" + (payload.api_key ? "（key 已明文写入 ~/.gloss/config.toml）" : "");
  } catch (e) {
    smsg.textContent = "保存失败: " + e;
  }
});

document.getElementById("stest").addEventListener("click", async () => {
  smsg.textContent = "测试中…（会真实调用一次模型，消耗极少量 token）";
  try {
    const d = await (await fetch("/api/config/test", { method: "POST" })).json();
    smsg.textContent = d.ok ? "✓ 连接正常，模型回复: " + d.reply : "✗ " + d.error;
  } catch (e) {
    smsg.textContent = "测试请求失败: " + e;
  }
});
</script>
</body>
</html>
"""

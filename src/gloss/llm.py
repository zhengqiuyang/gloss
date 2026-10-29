"""LLM layer: any OpenAI-compatible endpoint, JSON-validated glossary prompts."""

from __future__ import annotations

import base64
import json
import mimetypes
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import load_config, resolve_api_key
from .models import GlossError

TERM_SCHEMA_HINT = (
    '{"term": str, "aliases": [str], "one_liner": str, "detail": str, '
    '"tags": [str], "related": [str]}'
)
SNIPPET_SCHEMA_HINT = (
    '{"title": str, "one_liner": str, "detail": str, "tags": [str], '
    '"key_terms": [str]}'
)

TERM_SYSTEM = """You are gloss, the engine of a personal glossary. The user hands you a \
term they just ran into while reading; you produce a glossary card as strict JSON.

Rules:
- Reply with ONLY a JSON object — no markdown fences, no commentary — matching: {schema}
- one_liner: a single plain-language sentence that defines the term. Max 30 words. In {language}.
- detail: 2-4 concrete sentences in {language}, at most {max_chars} characters. Prefer a \
small example or "why it matters" over encyclopedic breadth.
- aliases: 2-5 alternative names or abbreviations; include the original English spelling \
if the term was given in another language.
- tags: 1-3 broad domains, lowercase, kebab-case (e.g. "machine-learning", "frontend").
- related: 2-5 closely related concepts. When a concept appears in known_terms, copy its \
spelling EXACTLY from known_terms so it links to the user's existing cards.
"""

SNIPPET_SYSTEM = """You are gloss, the engine of a personal glossary. The user pastes a \
chunk of text (or an image) they just read; you digest it into a snippet card as strict JSON.

Rules:
- Reply with ONLY a JSON object — no markdown fences, no commentary — matching: {schema}
- title: a short name for this snippet, max 12 words, in {language}.
- one_liner: the single most important takeaway, in {language}, max 30 words.
- detail: a faithful summary in {language}, at most {max_chars_summary} characters.
- tags: 1-3 broad domains, lowercase, kebab-case.
- key_terms: up to 8 concepts from the text worth glossing as their own cards later \
(technical terms, jargon, names of methods). When a concept appears in known_terms, \
copy its spelling EXACTLY from known_terms.
"""

TRANSLATE_SYSTEM = """You are a precise translation engine inside a personal glossary app.

Rules:
- Reply with ONLY a JSON object: {{"translation": str}}
- Translate the user's text faithfully and naturally into {target}.
- Keep code identifiers, file paths, URLs, and established technical terms untranslated.
- If the text is already in {target}, translate it into English instead.
"""


def extract_json(text: str) -> Dict[str, Any]:
    """Pull the first balanced JSON object out of an LLM reply, tolerating fences/prose."""
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    start = cleaned.find("{")
    if start == -1:
        raise GlossError(f"模型没有返回 JSON:\n{text[:300]}")
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    data = json.loads(cleaned[start : i + 1])
                except json.JSONDecodeError as exc:
                    raise GlossError(f"模型返回的 JSON 无法解析: {exc}") from exc
                if not isinstance(data, dict):
                    raise GlossError("模型返回的不是 JSON 对象")
                return data
    raise GlossError(f"模型返回的 JSON 不完整:\n{text[:300]}")


def _as_str_list(value: Any, max_items: int = 10) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()][:max_items]


class GlossLLM:
    """Thin wrapper over an OpenAI-compatible chat completion endpoint."""

    def __init__(self, cfg: Optional[Dict[str, Dict[str, Any]]] = None):
        self.cfg = cfg or load_config()

    @property
    def client(self):
        if getattr(self, "_client", None) is None:
            api_key = resolve_api_key(self.cfg)
            if not api_key:
                raise GlossError(
                    "还没有配置 API key。两种方式任选：\n"
                    "  1. export GLOSS_API_KEY=sk-...\n"
                    "  2. gloss config set llm.api_key sk-...  （注意 config 文件是明文）"
                )
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover
                raise GlossError("缺少 openai 依赖，请重新安装 gloss") from exc
            llm = self.cfg["llm"]
            self._client = OpenAI(
                base_url=llm["base_url"],
                api_key=api_key,
                timeout=llm["timeout"],
            )
        return self._client

    # -- low level ---------------------------------------------------------

    def _complete(
        self,
        system: str,
        user: str,
        image_path: Optional[str] = None,
    ) -> Tuple[str, int]:
        llm = self.cfg["llm"]
        if image_path:
            model = llm.get("vision_model") or llm["model"]
            content: List[Dict[str, Any]] = [
                {"type": "text", "text": user},
                {"type": "image_url", "image_url": {"url": _image_data_url(image_path)}},
            ]
        else:
            model = llm["model"]
            content = user
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ]
        client = self.client  # 未配 key 的友好报错在属性里，别让它被网络错误的包装盖住
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.2,
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            if "response_format" in str(exc):
                # Some OpenAI-compatible servers reject response_format; retry plain.
                resp = self.client.chat.completions.create(
                    model=model, messages=messages, temperature=0.2
                )
            else:
                raise GlossError(f"调用模型失败: {exc}") from exc
        usage = resp.usage
        tokens = getattr(usage, "total_tokens", 0) if usage else 0
        text = resp.choices[0].message.content or ""
        if not text.strip():
            raise GlossError("模型返回了空内容")
        return text, tokens

    def chat_json(
        self, system: str, user: str, image_path: Optional[str] = None
    ) -> Tuple[Dict[str, Any], int]:
        text, tokens = self._complete(system, user, image_path)
        return extract_json(text), tokens

    # -- glossary prompts ----------------------------------------------------

    def explain_term(
        self,
        term: str,
        known_terms: List[str],
        context: str = "",
        source: str = "",
    ) -> Tuple[Dict[str, Any], int]:
        g = self.cfg["glossary"]
        system = TERM_SYSTEM.format(
            schema=TERM_SCHEMA_HINT,
            language=g["language"],
            max_chars=g["detail_max_chars"],
        )
        lines = [f"term: {term}"]
        if context:
            lines.append(f"met_in: {context}")
        if source:
            lines.append(f"source: {source}")
        lines.append(f"known_terms: {json.dumps(known_terms, ensure_ascii=False)}")
        data, tokens = self.chat_json(system, "\n".join(lines))
        if not data.get("term"):
            data["term"] = term
        return data, tokens

    def digest_text(self, text: str, known_terms: List[str]) -> Tuple[Dict[str, Any], int]:
        g = self.cfg["glossary"]
        system = SNIPPET_SYSTEM.format(
            schema=SNIPPET_SCHEMA_HINT,
            language=g["language"],
            max_chars_summary=g["detail_max_chars"] * 3,
        )
        user = f"text:\n{text[:8000]}\n\nknown_terms: {json.dumps(known_terms, ensure_ascii=False)}"
        return self.chat_json(system, user)

    def analyze_image(
        self, image_path: str, note: str, known_terms: List[str]
    ) -> Tuple[Dict[str, Any], int]:
        g = self.cfg["glossary"]
        system = SNIPPET_SYSTEM.format(
            schema=SNIPPET_SCHEMA_HINT,
            language=g["language"],
            max_chars_summary=g["detail_max_chars"] * 3,
        )
        user = (
            "text: (see attached image — read it, then digest it)"
            + (f"\nuser note: {note}" if note else "")
            + f"\n\nknown_terms: {json.dumps(known_terms, ensure_ascii=False)}"
        )
        return self.chat_json(system, user, image_path=image_path)

    def translate(self, text: str, target: str) -> Tuple[str, int]:
        system = TRANSLATE_SYSTEM.format(target=target)
        data, tokens = self.chat_json(system, text[:8000])
        translation = str(data.get("translation") or "").strip()
        if not translation:
            raise GlossError("模型没有返回译文")
        return translation, tokens


def _image_data_url(path: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        raise GlossError(f"图片不存在: {p}")
    mime = mimetypes.guess_type(str(p))[0] or "image/png"
    data = base64.b64encode(p.read_bytes()).decode()
    return f"data:{mime};base64,{data}"


def get_client() -> GlossLLM:
    return GlossLLM()

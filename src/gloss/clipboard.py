"""Clipboard reading: text via pbpaste, images via the macOS pasteboard."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from .models import GlossError


def read_clipboard() -> Tuple[str, str]:
    """Return ("text", text) or ("image", png_path) from the clipboard.

    Text wins when both are present (copying a file copies its path as text).
    Raises GlossError when the clipboard holds neither.
    """
    text = _pbpaste()
    if text and text.strip():
        return "text", text.strip()

    png = _pasteboard_png()
    if png is not None:
        tmp = tempfile.NamedTemporaryFile(
            prefix="gloss-clipboard-", suffix=".png", delete=False
        )
        tmp.write(png)
        tmp.close()
        return "image", tmp.name

    raise GlossError(
        "剪贴板里没有文字也没有图片。先复制一个词/一段话/一张截图，再跑 gloss paste。"
    )


def _pbpaste() -> Optional[str]:
    try:
        result = subprocess.run(
            ["pbpaste"], capture_output=True, text=True, timeout=5
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    return result.stdout if result.returncode == 0 else None


def _pasteboard_png() -> Optional[bytes]:
    """Read raw PNG data from the macOS pasteboard via AppleScript (no deps)."""
    script = 'the clipboard as «class PNGf»'
    try:
        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=5
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    out = (result.stdout or "").strip()
    # AppleScript renders PNGf data as «data PNGf<hex>»
    if result.returncode != 0 or not (out.startswith("«data PNGf") and out.endswith("»")):
        return None
    hex_part = out.removeprefix("«data PNGf").removesuffix("»")
    try:
        return bytes.fromhex(hex_part)
    except ValueError:
        return None


def image_supported_hint() -> str:
    return (
        "图片解析需要一个视觉模型：配置 llm.vision_model"
        "（如 deepseek-flash、GLM-4V、gpt-4o-mini 等）。"
    )

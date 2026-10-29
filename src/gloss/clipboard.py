"""Clipboard reading: macOS pasteboard (pbpaste/osascript) and Windows
clipboard (Win32 + PowerShell). Other platforms need an adapter —
contributions welcome.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from typing import Optional, Tuple

from .models import GlossError


def read_clipboard() -> Tuple[str, str]:
    """Return ("text", text) or ("image", png_path) from the clipboard.

    Text wins when both are present (copying a file copies its path as text).
    Raises GlossError when the clipboard holds neither.
    """
    text = _clipboard_text()
    if text and text.strip():
        return "text", text.strip()

    png_path = _clipboard_png()
    if png_path is not None:
        return "image", png_path

    note = "（剪贴板捕获目前支持 macOS 和 Windows）" if sys.platform not in ("darwin", "win32") else ""
    raise GlossError(
        "剪贴板里没有文字也没有图片。先复制一个词/一段话/一张截图，再跑 gloss paste。" + note
    )


def _clipboard_text() -> Optional[str]:
    if sys.platform == "darwin":
        return _pbpaste()
    if sys.platform == "win32":
        return _win32_text()
    return None


def _clipboard_png() -> Optional[str]:
    """Return a path to a PNG of the clipboard image, or None."""
    if sys.platform == "darwin":
        return _macos_png_path()
    if sys.platform == "win32":
        return _win32_png_path()
    return None


# ---------------------------------------------------------------------------
# macOS
# ---------------------------------------------------------------------------

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


def _macos_png_path() -> Optional[str]:
    png = _pasteboard_png()
    if png is None:
        return None
    return _png_tempfile(png)


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------

def _win32_text() -> Optional[str]:
    """CF_UNICODETEXT straight from user32 — instant, no subprocess, no deps."""
    import ctypes
    from ctypes import wintypes

    CF_UNICODETEXT = 13
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.GetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    if not user32.OpenClipboard(None):
        return None
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


_WIN32_PNG_PS = """\
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$img = [System.Windows.Forms.Clipboard]::GetImage()
if ($img -eq $null) { exit 1 }
$img.Save($env:GLOSS_CLIP_PNG, [System.Drawing.Imaging.ImageFormat]::Png)
"""


def _win32_png_path() -> Optional[str]:
    """Save the clipboard image to a temp PNG via PowerShell.

    WinForms Clipboard requires an STA thread, hence -Sta. The target path is
    passed through the environment so no quoting can break the script.
    """
    import os

    tmp = tempfile.NamedTemporaryFile(
        prefix="gloss-clipboard-", suffix=".png", delete=False
    )
    tmp.close()
    env = {**os.environ, "GLOSS_CLIP_PNG": tmp.name}
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Sta",
             "-Command", _WIN32_PNG_PS],
            capture_output=True, text=True, timeout=15, env=env,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        _unlink(tmp.name)
        return None
    if result.returncode != 0:
        _unlink(tmp.name)
        return None
    return tmp.name


# ---------------------------------------------------------------------------
# shared
# ---------------------------------------------------------------------------

def _png_tempfile(data: bytes) -> str:
    tmp = tempfile.NamedTemporaryFile(
        prefix="gloss-clipboard-", suffix=".png", delete=False
    )
    tmp.write(data)
    tmp.close()
    return tmp.name


def _unlink(path: str) -> None:
    import os

    try:
        os.unlink(path)
    except OSError:
        pass


def image_supported_hint() -> str:
    return (
        "图片解析需要一个视觉模型：配置 llm.vision_model"
        "（如 deepseek-flash、GLM-4V、gpt-4o-mini 等）。"
    )

"""Windows global-hotkey popup: the `gloss app` counterpart for Windows.

Registers a system-wide hotkey via RegisterHotKey (no dependencies). On
trigger it ensures `gloss serve` is running, then opens a popup card:

- native (preferred): a frameless, always-on-top WebView2 window via
  pywebview — the same card HTML the macOS `gloss app` wraps, minus the
  browser chrome. Esc closes, the top strip drags.
- fallback: a Chrome/Edge `--app` window (still usable, but carries
  browser chrome).

The hotkey spec comes from the same config key as macOS (`app.hotkey`,
e.g. "ctrl+g"). "cmd" is read as the Win key on Windows; Win+G is
reserved by the Xbox Game Bar, so a failing registration falls back to
ctrl+g with a notice. Ctrl+Alt+Shift+G always quits the process.
"""

from __future__ import annotations

import ctypes
import os
import socket
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Optional, Tuple

from .config import load_config
from .models import GlossError

# RegisterHotKey modifiers
_MOD_MAP = {
    "ctrl": 0x0002, "control": 0x0002,
    "alt": 0x0001, "option": 0x0001, "opt": 0x0001,
    "shift": 0x0004,
    "cmd": 0x0008, "command": 0x0008, "win": 0x0008,
}
_MOD_SYMBOL = {
    "ctrl": "Ctrl", "control": "Ctrl",
    "alt": "Alt", "option": "Alt", "opt": "Alt",
    "shift": "Shift",
    "cmd": "Win", "command": "Win", "win": "Win",
}

WM_HOTKEY = 0x0312
CREATE_NO_WINDOW = 0x08000000
HOTKEY_ID, QUIT_ID = 1, 2
FALLBACK_SPEC = "ctrl+g"  # aligns with the macOS default ⌘G
QUIT_SPEC = "ctrl+alt+shift+g"
_CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

_POPUP: dict = {"win": None}
_NATIVE: Optional[bool] = None

_INJECT_JS = """
(function () {
  function bind() {
    var sb = document.querySelector('.searchbar');
    if (sb) sb.classList.add('pywebview-drag-region');
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && window.pywebview && window.pywebview.api) {
        window.pywebview.api.close_popup();
      }
    }, true);
  }
  if (document.readyState === 'complete') bind();
  else window.addEventListener('load', bind);
})();
"""


def parse_hotkey(spec: str) -> Optional[Tuple[int, int, str]]:
    """'ctrl+g' -> (modifiers, vk_code, display); None if invalid.

    Only single letters and digits are accepted as the key, matching the
    RegisterHotKey convention (VK codes 0x30-0x5A equal their ASCII value).
    """
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if len(parts) < 2:
        return None
    key = parts[-1]
    mods, display = 0, ""
    for m in parts[:-1]:
        if m not in _MOD_MAP:
            return None
        mods |= _MOD_MAP[m]
        display += _MOD_SYMBOL[m] + "+"
    if len(key) != 1 or not key.isalnum():
        return None
    return mods, ord(key.upper()), display + key.upper()


def _native_available() -> bool:
    """True when pywebview (WebView2) can provide the frameless popup."""
    global _NATIVE
    if _NATIVE is None:
        try:
            import webview  # noqa: F401

            _NATIVE = True
        except Exception:
            _NATIVE = False
    return _NATIVE


def run_hotkey() -> None:
    """Block forever with the hotkey registered; Ctrl+Alt+Shift+G quits."""
    if sys.platform != "win32":
        raise GlossError("gloss hotkey 目前仅支持 Windows；macOS 请用 gloss app")

    from .ui import console

    if _native_available():
        console.print("[green]📖 gloss 热键已启动[/green]（原生无边框卡片，呼出键见下 · Esc 关窗）")
        threading.Thread(target=_hotkey_loop, kwargs={"raise_errors": False}, daemon=True).start()
        import webview

        # hidden sentinel keeps the GUI loop alive; popups are created on demand
        webview.create_window(
            "gloss", html="<html></html>", width=1, height=1, frameless=True, hidden=True
        )
        webview.start()
    else:
        console.print(
            "[green]📖 gloss 热键已启动[/green]（呼出键见下 · 浏览器弹窗模式；"
            "安装 pywebview 可升级为无边框卡片: uv tool install --reinstall -e '.[app]'）"
        )
        _hotkey_loop(raise_errors=True)


def _hotkey_loop(raise_errors: bool) -> None:
    """Register the hotkeys, then pump messages until WM_QUIT / exit.

    Registration and the message pump must live on the same thread —
    hotkey messages are delivered to the registering thread.
    """
    user32 = ctypes.windll.user32
    from .ui import console

    spec = load_config()["app"]["hotkey"]
    parsed = parse_hotkey(spec)
    registered = parsed is not None and user32.RegisterHotKey(None, HOTKEY_ID, parsed[0], parsed[1])
    if not registered:
        parsed = parse_hotkey(FALLBACK_SPEC)
        if not user32.RegisterHotKey(None, HOTKEY_ID, parsed[0], parsed[1]):
            message = (
                f"全局热键注册失败（{FALLBACK_SPEC} 也被占用）。换个组合试试: "
                f"gloss config set app.hotkey ctrl+alt+g"
            )
            if raise_errors:
                raise GlossError(message)
            console.print(f"[red]✗ {message}[/red]")
            os._exit(1)
        console.print(
            f"[yellow]配置的热键 {spec!r} 注册失败（Win+G 被 Xbox Game Bar 占用？），"
            f"已改用 {FALLBACK_SPEC}（对齐 macOS 的 ⌘G）[/yellow]"
        )
    else:
        console.print(f"[dim]呼出键 {parsed[2]} · 退出键 Ctrl+Alt+Shift+G[/dim]")

    quit_parsed = parse_hotkey(QUIT_SPEC)
    if quit_parsed:
        user32.RegisterHotKey(None, QUIT_ID, quit_parsed[0], quit_parsed[1])

    msg = wintypes.MSG()
    ptr = ctypes.byref(msg)
    while user32.GetMessageW(ptr, None, 0, 0) > 0:
        if msg.message == WM_HOTKEY:
            if msg.wParam == HOTKEY_ID:
                threading.Thread(target=_trigger, daemon=True).start()
            elif msg.wParam == QUIT_ID:
                os._exit(0)
        user32.TranslateMessage(ptr)
        user32.DispatchMessageW(ptr)


def _trigger() -> None:
    port = load_config()["serve"]["port"]
    _ensure_serve(port)
    if _native_available():
        _show_native_popup(port)
    else:
        _open_browser_popup(port)


def _serve_up(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _ensure_serve(port: int) -> None:
    """Start `gloss serve` when down; wait until it accepts connections."""
    if _serve_up(port):
        return
    subprocess.Popen(
        [sys.executable, "-m", "gloss", "serve", "--no-browser"],
        creationflags=CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(40):  # a cold Python start takes a moment
        if _serve_up(port):
            return
        time.sleep(0.1)


# ---------------------------------------------------------------------------
# popups
# ---------------------------------------------------------------------------

class _PopupApi:
    """Exposed to the page as window.pywebview.api."""

    def close_popup(self) -> None:
        win = _POPUP.get("win")
        if win is not None:
            win.destroy()


def _show_native_popup(port: int) -> None:
    import webview

    old = _POPUP.get("win")
    if old is not None:
        try:
            old.destroy()
        except Exception:
            pass
        _POPUP["win"] = None

    win = webview.create_window(
        "gloss",
        f"http://127.0.0.1:{port}/?glass=1",
        js_api=_PopupApi(),
        width=560,
        height=430,
        frameless=True,
        on_top=True,
        easy_drag=False,
    )
    _POPUP["win"] = win

    def on_loaded() -> None:
        try:
            win.evaluate_js(_INJECT_JS)
        except Exception:
            pass

    def on_closed() -> None:
        if _POPUP.get("win") is win:
            _POPUP["win"] = None

    win.events.loaded += on_loaded
    win.events.closed += on_closed


def _open_browser_popup(port: int) -> None:
    for exe in _CHROME_PATHS:
        if os.path.exists(exe):
            browser = exe
            break
    else:
        browser = "msedge.exe"  # ships with Windows and supports --app too
    subprocess.Popen([browser, f"--app=http://127.0.0.1:{port}", "--window-size=640,720"])


def _open_popup(port: int) -> None:
    """Kept as the decision entry point (tests target this)."""
    if _native_available():
        _show_native_popup(port)
    else:
        _open_browser_popup(port)


# ---------------------------------------------------------------------------
# login autostart: a hidden launcher so the hotkey is alive after reboot
# ---------------------------------------------------------------------------

def startup_path() -> Path:
    startup = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return startup / "gloss-hotkey.vbs"


_VBS_TEMPLATE = 'CreateObject("Wscript.Shell").Run """{exe}"" hotkey", 0, False\n'


def install_startup() -> str:
    """Write the hidden autostart launcher; returns the resolved gloss.exe."""
    import shutil

    exe = shutil.which("gloss")
    if not exe:
        raise GlossError("PATH 里找不到 gloss，无法写入自启动")
    path = startup_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_VBS_TEMPLATE.format(exe=exe), encoding="ascii")
    return str(path)


def remove_startup() -> bool:
    path = startup_path()
    if path.exists():
        path.unlink()
        return True
    return False

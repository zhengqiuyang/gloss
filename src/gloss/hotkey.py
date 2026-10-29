"""Windows global-hotkey popup: the `gloss app` counterpart for Windows.

Registers a system-wide hotkey via RegisterHotKey (no dependencies). The
card window is created ONCE, hidden, at startup — with its events wired
before the GUI loop starts, so there are no show-time races — and every
hotkey press just reloads the page (fresh clipboard prefill) and shows it.
Esc/✕ hide the window; nothing is ever created or destroyed at press time.

The hotkey spec comes from the same config key as macOS (`app.hotkey`).
"cmd" is read as the Win key on Windows; Win+G is reserved by the Xbox
Game Bar, so a failing registration falls back to ctrl+g with a notice.
Ctrl+Alt+Shift+G quits the process.
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
_POPUP_TITLE = "gloss-card"  # distinct from the hidden sentinel, for FindWindow
_CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

_CARD: dict = {"win": None}
_NATIVE: Optional[bool] = None

_INJECT_CSS = """
#gloss-native-bar {
  position: fixed; top: 0; left: 0; right: 0; height: 30px;
  display: flex; align-items: center; justify-content: space-between;
  z-index: 999; -webkit-user-select: none; user-select: none;
}
#gloss-native-bar .gbar-drag {
  flex: 1; height: 100%;
  display: flex; align-items: center; padding-left: 12px;
}
#gloss-native-bar .gbar-title { font: 600 11px -apple-system, sans-serif; opacity: .45; }
#gloss-native-bar .gbar-x {
  width: 30px; height: 22px; margin-right: 4px;
  display: flex; align-items: center; justify-content: center;
  font-size: 13px; opacity: .55; border-radius: 6px; cursor: pointer;
}
#gloss-native-bar .gbar-x:hover { background: rgba(255,69,58,.15); color: #ff453a; opacity: 1; }
body.glass .app { padding-top: 36px !important; }
"""

_INJECT_JS = """
(function () {
  var css = document.createElement('style');
  css.textContent = `__CSS__`;
  document.head.appendChild(css);
  if (document.getElementById('gloss-native-bar')) return;
  function bind() {
    var bar = document.createElement('div');
    bar.id = 'gloss-native-bar';
    bar.innerHTML =
      '<span class="pywebview-drag-region gbar-drag">' +
        '<span class="gbar-title">📖 gloss — 拖动这里移动 · Esc 关闭</span>' +
      '</span>' +
      '<span class="gbar-x" id="gbar-x">✕</span>';
    document.body.appendChild(bar);
    function close() {
      if (window.pywebview && window.pywebview.api) window.pywebview.api.close_popup();
      else window.close();
    }
    bar.querySelector('#gbar-x').addEventListener('click', close);
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') close();
    }, true);
    var q = document.getElementById('q');
    if (q) q.focus();
  }
  if (document.readyState === 'complete') bind();
  else window.addEventListener('load', bind);
})();
""".replace("__CSS__", _INJECT_CSS.strip().replace("\n", " "))


def _start_tray() -> None:
    """📖 in the system tray — the counterpart of the macOS menu-bar item.

    Double-click pops the card, right-click quits. Skips silently when
    pystray/Pillow are not installed (the hotkey works either way).
    """
    try:
        import pystray
        from PIL import Image, ImageDraw
    except Exception:
        return

    def on_quit(icon, item) -> None:
        os._exit(0)

    def on_pop(icon, item) -> None:
        threading.Thread(target=_trigger, daemon=True).start()

    try:
        icon = pystray.Icon(
            "gloss",
            _tray_image(Image, ImageDraw),
            "gloss — 复制后 Ctrl+G 呼出卡片",
            pystray.Menu(
                pystray.MenuItem("呼出卡片", on_pop, default=True),
                pystray.MenuItem("退出", on_quit),
            ),
        )
        threading.Thread(target=icon.run, daemon=True).start()
    except Exception:
        return


def _tray_image(Image, ImageDraw):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([4, 4, 60, 60], radius=14, fill=(10, 132, 255, 255))
    try:
        from PIL import ImageFont

        font = ImageFont.truetype("segoeui.ttf", 38)
    except Exception:
        font = None
    if font is not None:
        d.text((32, 33), "g", font=font, fill="white", anchor="mm")
    else:
        d.text((26, 20), "g", fill="white")
    return img


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

    _start_tray()
    if _native_available():
        console.print("[green]📖 gloss 热键已启动[/green]（原生无边框卡片，呼出键见下 · Esc 关窗）")
        # the card is created once, hidden, with events wired BEFORE the GUI
        # loop starts — every later press only reloads and shows it
        _create_card(load_config()["serve"]["port"])
        threading.Thread(target=_hotkey_loop, kwargs={"raise_errors": False}, daemon=True).start()
        import webview

        # hidden sentinel keeps the GUI loop alive
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
                f"全局热键注册失败（{FALLBACK_SPEC} 也被占用）。可能已经有一个 "
                f"gloss hotkey 在运行（看系统托盘的 📖 图标）；想换组合: "
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
        _show_card(port)
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
# the card window (created once, reused forever)
# ---------------------------------------------------------------------------

class _PopupApi:
    """Exposed to the page as window.pywebview.api."""

    def close_popup(self) -> None:
        win = _CARD.get("win")
        if win is not None:
            win.hide()


def _create_card(port: int):
    """Create the hidden card window once; later calls return it unchanged."""
    import webview

    if _CARD.get("win") is not None:
        return _CARD["win"]

    win = webview.create_window(
        _POPUP_TITLE,
        f"http://127.0.0.1:{port}/?glass=1",
        js_api=_PopupApi(),
        width=560,
        height=430,
        frameless=True,
        on_top=True,
        easy_drag=False,
        hidden=True,
    )
    state = {"styled": False}

    # wired before the GUI loop starts, so this always runs before the first
    # Show() (GUI thread) — no race, and it governs handle re-creations too
    def on_before_show() -> None:
        try:
            form = getattr(win, "native", None)
            if form is not None:
                form.ShowInTaskbar = False
        except Exception:
            pass

    def on_loaded() -> None:
        try:
            win.evaluate_js(_INJECT_JS)
            if not state["styled"]:
                user32 = ctypes.windll.user32
                hwnd = user32.FindWindowW(None, _POPUP_TITLE)
                if hwnd:
                    GWL_EXSTYLE, WS_EX_TOOLWINDOW = -20, 0x00000080
                    user32.SetWindowLongW(
                        hwnd, GWL_EXSTYLE,
                        user32.GetWindowLongW(hwnd, GWL_EXSTYLE) | WS_EX_TOOLWINDOW,
                    )
                    state["styled"] = True
        except Exception:
            pass

    win.events.before_show += on_before_show
    win.events.loaded += on_loaded
    _CARD["win"] = win
    return win


def _show_card(port: int) -> None:
    win = _create_card(port)
    # reload: the page prefills from the clipboard and looks up on load
    win.load_url(f"http://127.0.0.1:{port}/?glass=1")
    win.show()
    # a hotkey-triggered window is born while another app owns the
    # foreground; without this the card never gets keyboard focus and
    # Esc is dead. the ALT tap defeats the foreground lock if needed.
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, _POPUP_TITLE)
        if hwnd:
            user32.SetForegroundWindow(hwnd)
            if user32.GetForegroundWindow() != hwnd:
                user32.keybd_event(0x12, 0, 0, 0)  # ALT down
                user32.SetForegroundWindow(hwnd)
                user32.keybd_event(0x12, 0, 2, 0)  # ALT up
    except Exception:
        pass


def _open_browser_popup(port: int) -> None:
    for exe in _CHROME_PATHS:
        if os.path.exists(exe):
            browser = exe
            break
    else:
        browser = "msedge.exe"  # ships with Windows and supports --app too
    subprocess.Popen([browser, f"--app=http://127.0.0.1:{port}", "--window-size=640,720"])


def _open_popup(port: int) -> None:
    """Decision entry point (tests target this)."""
    if _native_available():
        _show_card(port)
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


def detach() -> None:
    """Spawn a windowless resident `gloss hotkey` and return immediately.

    Prefers pythonw.exe so no console is ever created — nothing shows up
    in the taskbar no matter how this was launched.
    """
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = str(pythonw) if pythonw.exists() else sys.executable
    subprocess.Popen(
        [exe, "-m", "gloss", "hotkey"],
        creationflags=CREATE_NO_WINDOW,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=os.environ.get("SystemRoot", "."),
    )

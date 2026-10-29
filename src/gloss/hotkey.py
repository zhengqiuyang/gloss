"""Windows global-hotkey popup: the `gloss app` counterpart for Windows.

Registers a system-wide hotkey via RegisterHotKey (no dependencies). On
trigger it ensures `gloss serve` is running, then opens a frameless
browser `--app` window — the page pre-fills from the clipboard and looks
up immediately. Copy, hotkey, card.

The hotkey spec comes from the same config key as macOS (`app.hotkey`,
e.g. "ctrl+alt+g"). "cmd" is read as the Win key on Windows; Win+G is
reserved by the Xbox Game Bar, so a failing registration falls back to
ctrl+alt+g with a notice.
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
FALLBACK_SPEC = "ctrl+alt+g"
_CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def parse_hotkey(spec: str) -> Optional[Tuple[int, int, str]]:
    """'ctrl+alt+g' -> (modifiers, vk_code, display); None if invalid.

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


def run_hotkey() -> None:
    """Block forever with the hotkey registered; end the process to quit."""
    if sys.platform != "win32":
        raise GlossError("gloss hotkey 目前仅支持 Windows；macOS 请用 gloss app")

    user32 = ctypes.windll.user32
    spec = load_config()["app"]["hotkey"]
    parsed = parse_hotkey(spec)
    from .ui import console

    registered = parsed is not None and user32.RegisterHotKey(None, 1, parsed[0], parsed[1])
    if not registered:
        parsed = parse_hotkey(FALLBACK_SPEC)
        if not user32.RegisterHotKey(None, 1, parsed[0], parsed[1]):
            raise GlossError(
                f"全局热键注册失败（{FALLBACK_SPEC} 也被占用）。换个组合试试: "
                f"gloss config set app.hotkey ctrl+alt+shift+g"
            )
        console.print(
            f"[yellow]配置的热键 {spec!r} 注册失败（Win+G 被 Xbox Game Bar 占用？），"
            f"已改用 {FALLBACK_SPEC}[/yellow]"
        )
    else:
        console.print(
            f"[green]📖 gloss 热键已启动[/green]（{parsed[2]} 呼出弹窗，"
            f"自动查剪贴板内容；结束本进程即退出）"
        )

    msg = wintypes.MSG()
    ptr = ctypes.byref(msg)
    while user32.GetMessageW(ptr, None, 0, 0) > 0:
        if msg.message == WM_HOTKEY and msg.wParam == 1:
            threading.Thread(target=_trigger, daemon=True).start()
        user32.TranslateMessage(ptr)
        user32.DispatchMessageW(ptr)


def _trigger() -> None:
    port = load_config()["serve"]["port"]
    _ensure_serve(port)
    _open_popup(port)


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


def _open_popup(port: int) -> None:
    for exe in _CHROME_PATHS:
        if os.path.exists(exe):
            browser = exe
            break
    else:
        browser = "msedge.exe"  # ships with Windows and supports --app too
    subprocess.Popen([browser, f"--app=http://127.0.0.1:{port}", "--window-size=640,720"])


# ---------------------------------------------------------------------------
# login autostart: a hidden launcher so the hotkey is alive after reboot
# ---------------------------------------------------------------------------

def startup_path() -> "os.PathLike":
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

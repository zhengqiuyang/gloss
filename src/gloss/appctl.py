"""Lifecycle for the macOS menu-bar app: single instance, backgrounding, LaunchAgent.

`gloss app` detaches by default so the terminal can close and the menu-bar
item keeps working. This module owns the pid bookkeeping, the --stop /
--status commands, and the user-level LaunchAgent that relaunches gloss at
login (KeepAlive=false: quitting from the menu stays quit until the next
login — relaunching is opt-in again).

Everything here is stdlib-only and importable on any platform so the bookkeeping
can be unit-tested off-macOS; only the GUI itself needs pyobjc.
"""

from __future__ import annotations

import os
import plistlib
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from .config import gloss_home
from .models import GlossError

LAUNCH_LABEL = "com.gloss.app"
STOP_GRACE_SECONDS = 5.0
SPAWN_SETTLE_SECONDS = 1.5


# ---------------------------------------------------------------------------
# pid bookkeeping
# ---------------------------------------------------------------------------

def pid_path() -> Path:
    return gloss_home() / "app.pid"


def log_path() -> Path:
    return gloss_home() / "app.log"


def read_pid() -> Optional[int]:
    try:
        return int(pid_path().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def write_pid(pid: int) -> None:
    path = pid_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pid), encoding="utf-8")


def remove_pid() -> None:
    try:
        pid_path().unlink()
    except OSError:
        pass


def is_running(pid: Optional[int]) -> bool:
    """Existence check that never kills anything.

    os.kill(pid, 0) would be the POSIX idiom, but on Windows os.kill with an
    arbitrary signal TERMINATES the process — hence the OpenProcess branch.
    """
    if not pid or pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        exit_code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(exit_code))
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        return bool(ok) and exit_code.value == STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by another user
    except OSError:
        return False


def running_instance() -> Optional[int]:
    """pid of a live gloss app instance, or None (stale pid files cleaned up)."""
    pid = read_pid()
    if pid is None:
        return None
    if is_running(pid):
        return pid
    remove_pid()
    return None


# ---------------------------------------------------------------------------
# background launch / stop / status
# ---------------------------------------------------------------------------

def spawn_background(hidden: bool = True) -> int:
    """Detach `gloss app --foreground` from this terminal; returns the child pid.

    Launches via the absolute sys.executable path with no shell in between,
    so neither venv activation nor .zshrc is involved. Child output goes to
    app.log so a failed start is diagnosable through `gloss app --status`.
    """
    existing = running_instance()
    if existing is not None:
        raise GlossError(
            f"gloss app 已在运行 (pid {existing})，不重复创建。"
            "查看状态: gloss app --status · 停止: gloss app --stop"
        )
    args = [sys.executable, "-m", "gloss", "app", "--foreground"]
    if hidden:
        args.append("--hidden")
    log = log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "ab") as lf:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=lf,
            stderr=lf,
            start_new_session=(os.name != "nt"),  # survive the closing terminal
        )
    time.sleep(SPAWN_SETTLE_SECONDS)  # give a cold interpreter a moment
    if proc.poll() is not None:
        remove_pid()
        raise GlossError("gloss app 后台启动失败，app.log 最后几行:\n" + _log_tail())
    write_pid(proc.pid)  # child re-writes it with the same pid; this closes the race
    return proc.pid


def stop() -> Optional[int]:
    """Terminate the running instance; returns its pid, or None if none ran."""
    pid = running_instance()
    if pid is None:
        remove_pid()
        return None
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        remove_pid()
        return pid
    deadline = time.time() + STOP_GRACE_SECONDS
    while time.time() < deadline and is_running(pid):
        time.sleep(0.1)
    if is_running(pid) and os.name != "nt":  # SIGKILL is POSIX-only
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    remove_pid()
    return pid


def status() -> dict:
    pid = running_instance()
    return {
        "running": pid is not None,
        "pid": pid,
        "startup": startup_installed(),
        "log_tail": _log_tail(lines=3),
    }


def _log_tail(lines: int = 5) -> str:
    try:
        content = log_path().read_text(encoding="utf-8", errors="replace")
        return "\n".join(content.splitlines()[-lines:]) or "（空）"
    except OSError:
        return "（还没有日志）"


# ---------------------------------------------------------------------------
# login autostart: user-level LaunchAgent (no admin rights involved)
# ---------------------------------------------------------------------------

def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_LABEL}.plist"


def launchd_program_args() -> list:
    return [sys.executable, "-m", "gloss", "app", "--foreground", "--hidden"]


def launchd_plist() -> bytes:
    """The LaunchAgent payload.

    RunAtLoad starts gloss hidden at login; KeepAlive=false is deliberate —
    quitting from the menu-bar must NOT get the process instantly revived,
    it only comes back at the next login.
    """
    return plistlib.dumps(
        {
            "Label": LAUNCH_LABEL,
            "ProgramArguments": launchd_program_args(),
            "RunAtLoad": True,
            "KeepAlive": False,
            "ProcessType": "Interactive",
            "StandardOutPath": str(log_path()),
            "StandardErrorPath": str(log_path()),
        }
    )


def install_startup() -> Path:
    """Write the LaunchAgent and load it for the current user."""
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(launchd_plist())
    if sys.platform == "darwin":
        uid = os.getuid()
        if not _launchctl("bootstrap", f"gui/{uid}", str(path)):
            _launchctl("load", str(path))  # older macOS fallback
    return path


def remove_startup() -> bool:
    path = plist_path()
    if not path.exists():
        return False
    if sys.platform == "darwin":
        uid = os.getuid()
        _launchctl("bootout", f"gui/{uid}/{LAUNCH_LABEL}")
        _launchctl("unload", str(path))  # idempotent belt-and-braces
    path.unlink()
    return True


def startup_installed() -> bool:
    return plist_path().exists()


def _launchctl(*args: str) -> bool:
    if sys.platform != "darwin":
        return False
    try:
        subprocess.run(
            ["launchctl", *args],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False

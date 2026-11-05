"""Lifecycle for the macOS menu-bar app: single instance, backgrounding, LaunchAgent.

`gloss app` detaches by default so the terminal can close and the menu-bar
item keeps working. This module owns the process lock, the --stop /
--status commands, and the user-level LaunchAgent that relaunches gloss at
login (KeepAlive=false: quitting from the menu stays quit until the next
login — relaunching is opt-in again).

Everything here is stdlib-only and importable on any platform so the
bookkeeping can be unit-tested off-macOS; only the GUI itself needs pyobjc.
"""

from __future__ import annotations

import os
import plistlib
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

from .config import gloss_home
from .models import GlossError

LAUNCH_LABEL = "com.gloss.app"
STOP_GRACE_SECONDS = 5.0
SPAWN_SETTLE_SECONDS = 1.5


def _uid() -> int:
    """gui-uid for launchctl; 501 keeps the code testable on non-POSIX mocks."""
    return getattr(os, "getuid", lambda: 501)()


# ---------------------------------------------------------------------------
# pid bookkeeping (diagnostics only — the mutex is the InstanceLock below)
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


def _process_command(pid: int) -> str:
    """Best-effort command line; '' when the platform can't tell us."""
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["wmic", "process", "where", f"processid={pid}", "get", "commandline", "/value"],
                capture_output=True, text=True, timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            for line in (out.stdout or "").splitlines():
                if line.startswith("CommandLine="):
                    return line.split("=", 1)[1].strip()
            # wmic is deprecated / may be missing: fall back to PowerShell
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"],
                capture_output=True, text=True, timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return (out.stdout or "").strip()
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=5,
        )
        return (out.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def pid_is_gloss(pid: Optional[int]) -> bool:
    """True only when the command line identifies the process as gloss app.

    Unverifiable counts as NO: a stale pid may have been reused by any other
    program, and `gloss app --stop` must never signal an unidentified process.
    """
    if not pid:
        return False
    command = _process_command(pid).lower()
    if not command:
        return False
    return "gloss" in command and "app" in command


def running_instance() -> Optional[int]:
    """pid of a live gloss app instance, or None (stale/foreign pid cleaned up)."""
    pid = read_pid()
    if pid is None:
        return None
    if is_running(pid) and pid_is_gloss(pid):
        return pid
    remove_pid()
    return None


# ---------------------------------------------------------------------------
# the actual single-instance mutex
# ---------------------------------------------------------------------------

class InstanceLock:
    """Advisory whole-process lock; the second `gloss app` cannot acquire it.

    The handle stays open for the process's lifetime, so the lock is atomic
    (no check-then-write race) and the OS releases it even on a crash. The
    pid file stays around as diagnostics only.
    """

    def __init__(self) -> None:
        self._fh = None

    def acquire(self) -> bool:
        if self._fh is not None:
            return False
        path = gloss_home() / "app.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(path, "a+")
        try:
            fh.seek(0)
            if not fh.read(1):
                fh.write("0")  # lock a real byte (Windows wants a range)
                fh.flush()
            fh.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, PermissionError):
            fh.close()
            return False
        self._fh = fh
        try:  # record who holds it, diagnostics only
            fh.seek(0)
            fh.truncate()
            fh.write(str(os.getpid()))
            fh.flush()
        except OSError:
            pass
        return True

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            self._fh.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except (OSError, PermissionError):
            pass
        try:
            self._fh.close()
        except OSError:
            pass
        self._fh = None


# ---------------------------------------------------------------------------
# background launch / stop / status
# ---------------------------------------------------------------------------

def _spawn_args(hidden: bool, no_hotkey: bool = False) -> list:
    args = [sys.executable, "-m", "gloss", "app", "--foreground"]
    if hidden:
        args.append("--hidden")
    if no_hotkey:
        args.append("--no-hotkey")
    return args


def spawn_background(hidden: bool = True, no_hotkey: bool = False) -> int:
    """Detach `gloss app --foreground` from this terminal; returns the child pid.

    Launches via the absolute sys.executable path with no shell in between,
    so neither venv activation nor .zshrc is involved. Child output goes to
    app.log so a failed start is diagnosable through `gloss app --status`.
    The mutex itself is the child's InstanceLock — two concurrent spawns
    leave exactly one child alive; the loser exits with a message that the
    parent maps to a friendly error.
    """
    existing = running_instance()
    if existing is not None:
        raise GlossError(
            f"gloss app 已在运行 (pid {existing})，不重复创建。"
            "查看状态: gloss app --status · 停止: gloss app --stop"
        )
    args = _spawn_args(hidden=hidden, no_hotkey=no_hotkey)
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
        tail = _log_tail()
        if "已经在运行" in tail:
            raise GlossError(
                "gloss app 已在运行（并发启动只保留一个实例）。查看: gloss app --status"
            )
        raise GlossError("gloss app 后台启动失败，app.log 最后几行:\n" + tail)
    return proc.pid


def stop() -> Optional[int]:
    """Terminate the running instance; returns its pid, or None if none ran."""
    pid = running_instance()
    if pid is None:
        remove_pid()
        return None
    if not pid_is_gloss(pid):
        # a reused stale pid belongs to someone else's program now
        remove_pid()
        raise GlossError(
            f"pid {pid} 不是 gloss app（无法确认进程身份），已拒绝终止。"
            "悬空的 pid 记录已清理；找不到菜单栏图标的话直接 gloss app 重新启动即可。"
        )
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
        "startup_loaded": startup_loaded(),
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
    """Write the LaunchAgent AND load it; a load failure raises — the plist
    alone is not success."""
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(launchd_plist())
    if sys.platform == "darwin":
        ok, reason = _launchctl("bootstrap", f"gui/{_uid()}", str(path))
        if not ok:
            ok, fallback_reason = _launchctl("load", str(path))  # older macOS
            reason = reason or fallback_reason
        if not ok:
            raise GlossError(
                f"LaunchAgent 已写入但加载失败（{reason or '原因未知'}）。"
                f"可手动执行: launchctl load {path}，或重启后查看 app.log。"
            )
    return path


def remove_startup() -> bool:
    """Unload and delete the LaunchAgent.

    NOTE for macOS: if the running instance was started BY launchd, bootout
    terminates it — callers must check the pid afterwards and tell the user
    honestly instead of promising "当前实例继续运行".
    """
    path = plist_path()
    if not path.exists():
        return False
    if sys.platform == "darwin":
        _launchctl("bootout", f"gui/{_uid()}/{LAUNCH_LABEL}")
        _launchctl("unload", str(path))  # idempotent belt-and-braces
    path.unlink()
    return True


def startup_installed() -> bool:
    return plist_path().exists()


def startup_loaded() -> Optional[bool]:
    """Whether launchd currently has the agent loaded; None = can't tell."""
    if sys.platform != "darwin":
        return None
    ok, _ = _launchctl("print", f"gui/{_uid()}/{LAUNCH_LABEL}")
    return ok


def _launchctl(*args: str) -> Tuple[bool, str]:
    """Run launchctl and report (succeeded, reason). Non-darwin: no-op success."""
    if sys.platform != "darwin":
        return True, ""
    try:
        proc = subprocess.run(
            ["launchctl", *args],
            capture_output=True,
            text=True,
            timeout=10,
        )
        reason = (proc.stderr or proc.stdout or "").strip()
        return proc.returncode == 0, reason
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)

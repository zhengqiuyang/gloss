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

import hmac
import json
import os
import plistlib
import secrets
import socket
import socketserver
import subprocess
import sys
import time
import tempfile
import threading
from pathlib import Path
from typing import Callable, Optional, Tuple

from .config import gloss_home
from .models import GlossError

LAUNCH_LABEL = "com.gloss.app"
STOP_GRACE_SECONDS = 5.0
STARTUP_TIMEOUT_SECONDS = 15.0
CONTROL_TIMEOUT_SECONDS = 1.0


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


def remove_pid(expected_pid: Optional[int] = None) -> None:
    if expected_pid is not None and read_pid() != expected_pid:
        return
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


def _instance_path() -> Path:
    return gloss_home() / "app.instance.json"


def _read_instance() -> Optional[dict]:
    try:
        info = json.loads(_instance_path().read_text(encoding="utf-8"))
        if (isinstance(info, dict) and type(info.get("pid")) is int
                and info["pid"] > 0 and type(info.get("port")) is int
                and 0 < info["port"] < 65536 and isinstance(info.get("token"), str)
                and len(info["token"]) == 64 and info["token"].isascii()):
            return info
    except (OSError, ValueError):
        pass
    return None


def _request_instance(info: dict, action: str = "ping") -> bool:
    """Authenticate the instance itself, never infer identity from a process name."""
    try:
        with socket.create_connection(("127.0.0.1", info["port"]),
                                      timeout=CONTROL_TIMEOUT_SECONDS) as sock:
            sock.sendall(json.dumps({"pid": info["pid"], "token": info["token"], "action": action}).encode() + b"\n")
            with sock.makefile("rb") as reader:
                reply = json.loads(reader.readline(2048))
        return (isinstance(reply, dict) and reply.get("pid") == info["pid"]
                and reply.get("ok") is True and reply.get("token") == info["token"])
    except (OSError, ValueError):
        return False


def pid_is_gloss(pid: Optional[int]) -> bool:
    info = _read_instance()
    return bool(info and info["pid"] == pid and _request_instance(info))


def running_instance() -> Optional[int]:
    # Observers never delete another process's registration. A new lock owner
    # replaces stale records; even a missing diagnostic PID cannot hide it.
    info = _read_instance()
    return info["pid"] if info and _request_instance(info) else None


class InstanceControl:
    """Private loopback endpoint for readiness and graceful native termination.

    Only a random per-launch token can request a stop. Legacy/stale PID files
    cannot cause signals to unrelated processes. The registration is mode 0600.
    """

    def __init__(self, on_stop: Callable[[], None]) -> None:
        self.info = {"pid": os.getpid(), "token": secrets.token_hex(32)}
        self.on_stop = on_stop
        self.server = None
        self.thread = None

    def start(self) -> None:
        owner = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                self.request.settimeout(CONTROL_TIMEOUT_SECONDS)
                try:
                    data = json.loads(self.rfile.readline(2048))
                    if not isinstance(data, dict) or data.get("pid") != owner.info["pid"]:
                        return
                    token = data.get("token")
                    if (not isinstance(token, str) or not token.isascii()
                            or not hmac.compare_digest(token, owner.info["token"])):
                        return
                    action = data.get("action")
                    if action not in ("ping", "stop"):
                        return
                    reply = dict(owner.info, ok=True)
                    self.wfile.write(json.dumps(reply).encode() + b"\n")
                    self.wfile.flush()
                    if action == "stop":
                        owner.on_stop()
                except (OSError, ValueError):
                    return

        self.server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
        self.info["port"] = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self.thread.start()
        path = _instance_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=".app-instance-", delete=False) as fh:
                temp = Path(fh.name)
                json.dump(self.info, fh)
            os.replace(temp, path)
        except OSError:
            self.close()
            raise
        finally:
            if "temp" in locals():
                temp.unlink(missing_ok=True)

    def close(self) -> None:
        if _read_instance() == self.info:
            _instance_path().unlink(missing_ok=True)
        server, self.server = self.server, None
        if server is not None:
            server.shutdown()
            server.server_close()
        thread, self.thread = self.thread, None
        if thread is not None:
            thread.join(timeout=CONTROL_TIMEOUT_SECONDS)


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

    def acquire(self, record_pid: bool = True) -> bool:
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
        if not record_pid:
            return True
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
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            # The losing launcher owns neither the PID nor the control record.
            # Its cleanup must not erase the winner's registration.
            if running_instance() is not None:
                raise GlossError("gloss app 已在运行（并发启动只保留一个实例）。")
            if "已经在运行" in _log_tail():
                # The winner may still be importing Cocoa; confirm its
                # authenticated readiness before reporting a duplicate.
                time.sleep(0.1)
                continue
            raise GlossError("gloss app 后台启动失败，app.log 最后几行:\n" + _log_tail())
        if running_instance() == proc.pid:
            return proc.pid
        time.sleep(0.1)
    # This is our unreaped child, not a PID recovered from a stale file.
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    raise GlossError("gloss app 启动超时，尚未就绪。请查看 app.log。")


def wait_stopped(info: dict) -> bool:
    deadline = time.monotonic() + STOP_GRACE_SECONDS
    while time.monotonic() < deadline:
        current = _read_instance()
        if not is_running(info["pid"]) or (current is not None and current != info):
            return True
        probe = InstanceLock()
        if probe.acquire(record_pid=False):
            probe.release()
            return True
        time.sleep(0.1)
    return False


def stop() -> Optional[int]:
    """Ask an authenticated instance to quit; never signal a recovered PID."""
    info = _read_instance()
    if info is None or not _request_instance(info):
        pid = read_pid()
        if pid is not None and is_running(pid):
            raise GlossError(
                "无法确认 gloss 实例身份，已拒绝终止任何进程。"
                "旧版本请从菜单栏退出；如进程已退出，可直接重新启动 gloss app。"
            )
        return None
    if not _request_instance(info, "stop"):
        raise GlossError("gloss app 未确认退出请求，请查看菜单栏和 app.log。")
    if not wait_stopped(info):
        raise GlossError("gloss app 尚未退出；为避免误杀，不按 PID 强制终止，请从菜单栏退出。")
    return info["pid"]


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


def launchd_program_args(no_hotkey: bool = False) -> list:
    return _spawn_args(hidden=True, no_hotkey=no_hotkey)


def launchd_plist(no_hotkey: bool = False) -> bytes:
    """The LaunchAgent payload.

    RunAtLoad starts gloss hidden at login; KeepAlive=false is deliberate —
    quitting from the menu-bar must NOT get the process instantly revived,
    it only comes back at the next login.
    """
    return plistlib.dumps(
        {
            "Label": LAUNCH_LABEL,
            "ProgramArguments": launchd_program_args(no_hotkey=no_hotkey),
            "RunAtLoad": True,
            "KeepAlive": False,
            "ProcessType": "Interactive",
            "EnvironmentVariables": {"GLOSS_HOME": str(gloss_home().resolve())},
            "StandardOutPath": str(log_path().resolve()),
            "StandardErrorPath": str(log_path().resolve()),
        }
    )


def install_startup(no_hotkey: bool = False) -> Path:
    """Load the current payload once; refresh a loaded agent only when changed."""
    path = plist_path()
    payload = launchd_plist(no_hotkey=no_hotkey)
    loaded = startup_loaded() if sys.platform == "darwin" else False
    if loaded is None:
        raise GlossError("无法查询 LaunchAgent 状态，未修改登录自启。请查看 launchctl print。")
    if loaded and path.exists() and path.read_bytes() == payload:
        return path
    if loaded:
        ok, reason = _launchctl("bootout", f"gui/{_uid()}/{LAUNCH_LABEL}")
        if not ok:
            raise GlossError(f"无法更新已加载的 LaunchAgent：{reason}")
    path.parent.mkdir(parents=True, exist_ok=True)
    log_path().parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    if sys.platform == "darwin":
        ok, reason = _launchctl("bootstrap", f"gui/{_uid()}", str(path))
        if not ok:
            ok, fallback_reason = _launchctl("load", str(path))
            reason = fallback_reason or reason
        if not ok and startup_loaded() is not True:
            raise GlossError(f"LaunchAgent 已写入但加载失败（{reason or '原因未知'}）。请查看 app.log。")
    return path


def ensure_startup_running() -> int:
    """Let launchd own startup; do not race it with an unrelated Popen."""
    existing = running_instance()
    if existing is not None:
        return existing
    ok, reason = _launchctl("kickstart", f"gui/{_uid()}/{LAUNCH_LABEL}")
    if not ok and "timed out" not in reason.lower():
        raise GlossError(f"LaunchAgent 已加载但无法启动：{reason}")
    # launchd may throttle an immediate restart. A timed-out kickstart client
    # does not mean the managed process failed; authenticate readiness next.
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        pid = running_instance()
        if pid is not None:
            return pid
        time.sleep(0.1)
    raise GlossError("LaunchAgent 尚未就绪，请查看 app.log；登录自启配置仍保留。")


def remove_startup() -> bool:
    """Unload before removing the plist; failures retain the configured file."""
    path = plist_path()
    loaded = startup_loaded() if sys.platform == "darwin" else False
    if loaded is None:
        raise GlossError("无法查询 LaunchAgent 状态，未移除登录自启。")
    if not path.exists() and not loaded:
        return False
    if loaded:
        ok, reason = _launchctl("bootout", f"gui/{_uid()}/{LAUNCH_LABEL}")
        if not ok:
            raise GlossError(f"LaunchAgent 卸载失败，未移除配置：{reason}")
    path.unlink(missing_ok=True)
    return True


def startup_installed() -> bool:
    return plist_path().exists()


def startup_loaded() -> Optional[bool]:
    """Whether launchd currently has the agent loaded; None = can't tell."""
    if sys.platform != "darwin":
        return None
    ok, reason = _launchctl("print", f"gui/{_uid()}/{LAUNCH_LABEL}")
    if ok:
        return True
    if any(text in reason.lower() for text in
           ("could not find service", "could not find specified service", "no such process")):
        return False
    return None


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

"""appctl lifecycle bookkeeping — pid files, LaunchAgent payload, status/stop.

Runs on any OS: the darwin-only parts (launchctl bootstrap, the GUI itself)
are guarded, so the bookkeeping is verified everywhere.
"""

from __future__ import annotations

import os
import plistlib
import sys

import pytest

from gloss import appctl
from gloss.cli import cli
from gloss.models import GlossError


def _isolate_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))


# -- pid bookkeeping ---------------------------------------------------------


def test_pid_roundtrip(home):
    assert appctl.read_pid() is None
    appctl.write_pid(4321)
    assert appctl.read_pid() == 4321
    appctl.remove_pid()
    assert appctl.read_pid() is None


def test_is_running_is_safe_and_accurate(home):
    assert appctl.is_running(None) is False
    assert appctl.is_running(0) is False
    assert appctl.is_running(-5) is False
    assert appctl.is_running(os.getpid()) is True


def test_running_instance_cleans_stale_pid(home, monkeypatch):
    appctl.write_pid(999999999)
    monkeypatch.setattr(appctl, "is_running", lambda pid: False)
    assert appctl.running_instance() is None
    assert appctl.read_pid() is None  # 陈旧 pid 文件被顺手清掉


# -- LaunchAgent payload ------------------------------------------------------


def test_launchd_plist_roundtrips(home):
    data = plistlib.loads(appctl.launchd_plist())
    assert data["Label"] == appctl.LAUNCH_LABEL
    assert data["RunAtLoad"] is True
    assert data["KeepAlive"] is False  # 菜单栏退出后不被立即拉起，下次登录才再启动
    args = data["ProgramArguments"]
    assert os.path.isabs(args[0])  # 绝对路径：不依赖 venv 激活或 .zshrc
    assert args[1:4] == ["-m", "gloss", "app"]
    assert "--hidden" in args and "--foreground" in args
    assert data["StandardOutPath"] == str(appctl.log_path())
    assert data["StandardErrorPath"] == str(appctl.log_path())


def test_install_remove_startup_roundtrip(home, tmp_path, monkeypatch):
    _isolate_home(monkeypatch, tmp_path)
    assert appctl.startup_installed() is False
    path = appctl.install_startup()
    assert path.exists()
    assert appctl.startup_installed() is True
    assert plistlib.loads(path.read_bytes())["Label"] == appctl.LAUNCH_LABEL
    assert appctl.remove_startup() is True
    assert appctl.startup_installed() is False
    assert appctl.remove_startup() is False  # 幂等


def test_status_reports_not_running(home, tmp_path, monkeypatch):
    _isolate_home(monkeypatch, tmp_path)
    st = appctl.status()
    assert st["running"] is False
    assert st["startup"] is False
    assert "pid" in st and "log_tail" in st


def test_spawn_refuses_second_instance(home, monkeypatch):
    monkeypatch.setattr(appctl, "running_instance", lambda: 123)
    with pytest.raises(GlossError) as exc:
        appctl.spawn_background()
    assert "已在运行" in str(exc.value)


# -- 单实例互斥（P1）：进程级文件锁，并发启动只有一个能拿到 -------------------


def test_instance_lock_is_mutually_exclusive(home):
    a, b = appctl.InstanceLock(), appctl.InstanceLock()
    assert a.acquire() is True
    assert b.acquire() is False  # 并发启动的第二个实例拿不到锁
    a.release()
    assert b.acquire() is True   # 释放后可获取
    b.release()


def test_instance_lock_records_holder_pid(home):
    lock = appctl.InstanceLock()
    assert lock.acquire() is True
    lock.release()  # Windows 持锁期间其他句柄读不了锁定字节，释放后再验证
    assert (appctl.gloss_home() / "app.lock").read_text(encoding="utf-8") == str(os.getpid())


def test_spawn_args_forward_flags():
    plain = appctl._spawn_args(hidden=False, no_hotkey=False)
    assert "--hidden" not in plain and "--no-hotkey" not in plain
    assert os.path.isabs(plain[0])
    both = appctl._spawn_args(hidden=True, no_hotkey=True)
    assert "--hidden" in both and "--no-hotkey" in both


def test_spawn_forwards_flags_to_child(home, monkeypatch):
    captured = {}

    class FakeProc:
        pid = 4321

        def poll(self):
            return None

    monkeypatch.setattr(appctl, "running_instance", lambda: None)
    monkeypatch.setattr(appctl.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        appctl.subprocess, "Popen",
        lambda args, **kw: captured.update(args=args) or FakeProc(),
    )
    appctl.spawn_background(hidden=True, no_hotkey=True)
    assert "--no-hotkey" in captured["args"] and "--hidden" in captured["args"]


# -- --stop 的身份校验（P1）：pid 被复用时绝不向陌生进程发信号 ----------------


def test_pid_identity_check(home, monkeypatch):
    monkeypatch.setattr(
        appctl, "_process_command",
        lambda pid: "/usr/bin/python3 -m gloss app --foreground --hidden",
    )
    assert appctl.pid_is_gloss(123) is True
    monkeypatch.setattr(appctl, "_process_command", lambda pid: "/usr/sbin/cron")
    assert appctl.pid_is_gloss(123) is False
    monkeypatch.setattr(appctl, "_process_command", lambda pid: "")
    assert appctl.pid_is_gloss(123) is False
    assert appctl.pid_is_gloss(None) is False


def test_stop_refuses_unidentified_pid(home, monkeypatch):
    """纵深防御：即使 running_instance 放行（两次检查之间身份翻转），
    stop 发信号前仍要确认身份，否则拒绝并清理悬空记录。"""
    appctl.write_pid(os.getpid())
    monkeypatch.setattr(appctl, "running_instance", lambda: os.getpid())
    monkeypatch.setattr(appctl, "pid_is_gloss", lambda pid: False)
    with pytest.raises(GlossError) as exc:
        appctl.stop()
    assert "拒绝" in str(exc.value)
    assert appctl.read_pid() is None  # 悬空记录清理，且没有杀任何进程


def test_running_instance_ignores_foreign_pid(home, monkeypatch):
    appctl.write_pid(os.getpid())
    monkeypatch.setattr(appctl, "pid_is_gloss", lambda pid: False)
    assert appctl.running_instance() is None


# -- LaunchAgent 加载状态（P2）：返回码要检查，失败要报原因 --------------------


def test_launchctl_reports_nonzero_exit(home, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")  # 非 darwin 会短路成功

    class FakeProc:
        returncode = 5
        stderr = "Load error: 5"
        stdout = ""

    monkeypatch.setattr(appctl.subprocess, "run", lambda *a, **kw: FakeProc())
    ok, reason = appctl._launchctl("bootstrap", "gui/501/x")
    assert ok is False
    assert "5" in reason


def test_install_startup_reports_load_failure(home, tmp_path, monkeypatch):
    _isolate_home(monkeypatch, tmp_path)
    real_platform = sys.platform
    monkeypatch.setattr(sys, "platform", "darwin")  # 让 bootstrap 路径真正执行
    monkeypatch.setattr(appctl, "_launchctl", lambda *a: (False, "Load error: 5"))
    with pytest.raises(GlossError) as exc:
        appctl.install_startup()
    assert "加载失败" in str(exc.value) and "5" in str(exc.value)
    monkeypatch.setattr(sys, "platform", real_platform)  # 恢复真实平台语义
    assert appctl.startup_installed() is True      # plist 已写入
    assert appctl.startup_loaded() is None         # 非 macOS 查不到加载态


# -- CLI surface ---------------------------------------------------------------


def test_app_status_cli_when_down(runner, home, tmp_path, monkeypatch):
    _isolate_home(monkeypatch, tmp_path)
    result = runner.invoke(cli, ["app", "--status"])
    assert result.exit_code == 0, result.output
    assert "未运行" in result.output
    assert "登录自启" in result.output


def test_app_stop_cli_when_down(runner, home):
    result = runner.invoke(cli, ["app", "--stop"])
    assert result.exit_code == 0, result.output
    assert "没有在运行" in result.output


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS 上该命令合法")
def test_app_startup_rejected_off_mac(runner, home):
    result = runner.invoke(cli, ["app", "--startup"])
    assert result.exit_code != 0
    assert "gloss hotkey" in str(result.exception)  # 指到 Windows 的对应能力


@pytest.mark.skipif(sys.platform == "darwin", reason="真实 mac 上应实测 bootout 行为")
def test_app_no_startup_reports_instance_fate(runner, home, monkeypatch):
    from gloss import appctl as appctl_mod

    monkeypatch.setattr(sys, "platform", "darwin")  # 走 remove 分支的完整逻辑
    monkeypatch.setattr(appctl_mod, "remove_startup", lambda: True)
    monkeypatch.setattr(appctl_mod, "running_instance", lambda: 42)
    # launchd 托管的实例随 bootout 停止 → 如实告知 + 重启指引
    monkeypatch.setattr(appctl_mod, "is_running", lambda pid: False)
    result = runner.invoke(cli, ["app", "--no-startup"])
    assert result.exit_code == 0, result.output
    assert "随服务卸载停止" in result.output
    assert "gloss app" in result.output
    # 手动启动的实例则承诺继续运行
    monkeypatch.setattr(appctl_mod, "is_running", lambda pid: True)
    result = runner.invoke(cli, ["app", "--no-startup"])
    assert result.exit_code == 0, result.output
    assert "继续运行" in result.output

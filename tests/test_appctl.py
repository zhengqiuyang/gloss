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

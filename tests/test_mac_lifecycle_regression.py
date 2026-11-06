"""Lifecycle regressions: a failed launcher and foreign PIDs must be harmless."""
from __future__ import annotations

import os
import sys

import pytest

from gloss import appctl
from gloss.models import GlossError


def test_failed_launcher_preserves_other_instance_pid(home, monkeypatch):
    class FailedChild:
        pid = 999
        def poll(self):
            return 1

    appctl.write_pid(456)
    monkeypatch.setattr(appctl, 'running_instance', lambda: None)
    monkeypatch.setattr(appctl.subprocess, 'Popen', lambda *a, **kw: FailedChild())
    monkeypatch.setattr(appctl.time, 'sleep', lambda _: None)
    monkeypatch.setattr(appctl, '_log_tail', lambda: 'gloss app 后台启动失败')
    with pytest.raises(GlossError):
        appctl.spawn_background()
    assert appctl.read_pid() == 456


def test_name_substrings_are_not_process_identity(home, monkeypatch):
    monkeypatch.setattr(appctl, '_process_command',
                        lambda pid: '/usr/bin/python3 /tmp/gloss/app_backup.py', raising=False)
    appctl.write_pid(os.getpid())
    assert appctl.pid_is_gloss(os.getpid()) is False


def test_enabling_loaded_startup_is_idempotent(home, tmp_path, monkeypatch):
    path = tmp_path / 'agent.plist'
    path.write_bytes(appctl.launchd_plist())
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setattr(appctl, 'plist_path', lambda: path)
    monkeypatch.setattr(appctl, 'startup_loaded', lambda: True)
    calls = []
    monkeypatch.setattr(appctl, '_launchctl', lambda *args: calls.append(args) or (False, 'already loaded'))
    assert appctl.install_startup() == path
    assert calls == []


def test_control_rejects_wrong_token_and_pid(home):
    from threading import Event
    stopped = Event()
    control = appctl.InstanceControl(stopped.set)
    control.start()
    try:
        assert appctl.running_instance() == os.getpid()
        assert not appctl._request_instance(dict(control.info, token='0' * 64), 'stop')
        assert not appctl._request_instance(dict(control.info, pid=os.getpid() + 1), 'stop')
        assert not stopped.is_set()
        assert appctl._request_instance(control.info, 'stop')
        assert stopped.wait(1)
        if os.name == 'posix':
            assert appctl._instance_path().stat().st_mode & 0o777 == 0o600
    finally:
        control.close()
    assert appctl.running_instance() is None


def test_stop_uses_control_without_signaling_registered_pid(home, monkeypatch):
    import threading
    control = appctl.InstanceControl(lambda: threading.Thread(target=control.close).start())
    control.start()
    appctl.write_pid(os.getpid())
    signals = []
    monkeypatch.setattr(appctl.os, 'kill', lambda *args: signals.append(args))
    assert appctl.stop() == os.getpid()
    # kill(pid, 0) is an existence query; no termination signal is allowed.
    assert all(sig == 0 for _, sig in signals)
    control.close()


def test_pid_cleanup_only_removes_owners_record(home):
    appctl.write_pid(456)
    appctl.remove_pid(expected_pid=999)
    assert appctl.read_pid() == 456
    appctl.remove_pid(expected_pid=456)
    assert appctl.read_pid() is None


def test_control_identity_survives_missing_diagnostic_pid(home):
    control = appctl.InstanceControl(lambda: None)
    control.start()
    try:
        assert appctl.read_pid() is None
        assert appctl.running_instance() == os.getpid()
    finally:
        control.close()


def test_real_subprocess_lock_is_exclusive(home):
    import subprocess
    lock = appctl.InstanceLock()
    assert lock.acquire()
    code = ('from gloss.appctl import InstanceLock; '
            'lock=InstanceLock(); print(lock.acquire(), flush=True)')
    try:
        result = subprocess.run([sys.executable, '-c', code],
                                capture_output=True, text=True, check=True)
        assert result.stdout.strip() == 'False'
    finally:
        lock.release()
    result = subprocess.run([sys.executable, '-c', code],
                            capture_output=True, text=True, check=True)
    assert result.stdout.strip() == 'True'


def test_spawn_waits_for_ready_identity(home, monkeypatch):
    class StartingChild:
        pid = 999
        def poll(self):
            return None
    identities = iter([None, None, 999])
    monkeypatch.setattr(appctl, 'running_instance', lambda: next(identities))
    monkeypatch.setattr(appctl.subprocess, 'Popen', lambda *a, **kw: StartingChild())
    sleeps = []
    monkeypatch.setattr(appctl.time, 'sleep', sleeps.append)
    assert appctl.spawn_background() == 999
    assert len(sleeps) == 1


def test_unknown_launchd_status_is_not_reported_unloaded(home, monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setattr(appctl, '_launchctl', lambda *a: (False, 'permission denied'))
    assert appctl.startup_loaded() is None
    with pytest.raises(GlossError, match='无法查询'):
        appctl.install_startup()


def test_launchd_missing_service_and_loaded_status(home, monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setattr(appctl, '_launchctl', lambda *a: (False, 'Could not find service x in domain'))
    assert appctl.startup_loaded() is False
    monkeypatch.setattr(appctl, '_launchctl', lambda *a: (True, 'service = {}'))
    assert appctl.startup_loaded() is True


def test_failed_bootout_retains_startup_file(home, tmp_path, monkeypatch):
    path = tmp_path / 'agent.plist'
    path.write_bytes(appctl.launchd_plist())
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setattr(appctl, 'plist_path', lambda: path)
    monkeypatch.setattr(appctl, 'startup_loaded', lambda: True)
    monkeypatch.setattr(appctl, '_launchctl', lambda *a: (False, 'permission denied'))
    with pytest.raises(GlossError, match='卸载失败'):
        appctl.remove_startup()
    assert path.exists()


def test_launchd_preserves_isolated_home_without_forwarding_secrets(home, monkeypatch):
    import plistlib
    monkeypatch.setenv('GLOSS_API_KEY', 'test-secret-not-to-forward')
    payload = plistlib.loads(appctl.launchd_plist())
    assert payload['EnvironmentVariables'] == {'GLOSS_HOME': str(home.resolve())}
    assert 'test-secret-not-to-forward' not in str(payload)


def test_stop_is_not_confirmed_until_instance_mutex_is_free(home, monkeypatch):
    lock = appctl.InstanceLock()
    assert lock.acquire()
    info = {'pid': os.getpid(), 'port': 1, 'token': 'a' * 64}
    monkeypatch.setattr(appctl, '_read_instance', lambda: None)
    monkeypatch.setattr(appctl, 'STOP_GRACE_SECONDS', 0.02)
    try:
        assert appctl.wait_stopped(info) is False
    finally:
        lock.release()
    assert appctl.wait_stopped(info) is True


def test_startup_waits_for_launchd_instead_of_spawning_competitor(home, runner, monkeypatch):
    from gloss.cli import cli
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setattr(appctl, 'install_startup', lambda **kw: 'agent.plist')
    monkeypatch.setattr(appctl, 'ensure_startup_running', lambda: 123, raising=False)
    monkeypatch.setattr(appctl, 'running_instance', lambda: None)
    def competitor(**kw):
        pytest.fail('must not race launchd with a second launcher')
    monkeypatch.setattr(appctl, 'spawn_background', competitor)
    result = runner.invoke(cli, ['app', '--startup', '--no-hotkey'])
    assert result.exit_code == 0, result.output
    assert '123' in result.output


def test_startup_payload_forwards_no_hotkey(home):
    import plistlib
    payload = plistlib.loads(appctl.launchd_plist(no_hotkey=True))
    assert '--no-hotkey' in payload['ProgramArguments']


def test_kickstart_timeout_still_checks_authenticated_readiness(home, monkeypatch):
    identities = iter([None, None, 123])
    monkeypatch.setattr(appctl, 'running_instance', lambda: next(identities))
    monkeypatch.setattr(appctl, '_launchctl', lambda *a: (False, 'kickstart timed out after 10 seconds'))
    monkeypatch.setattr(appctl.time, 'sleep', lambda _: None)
    assert appctl.ensure_startup_running() == 123


def test_removing_startup_without_instance_does_not_claim_it_is_running(home, runner, monkeypatch):
    from gloss.cli import cli
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setattr(appctl, 'running_instance', lambda: None)
    monkeypatch.setattr(appctl, 'remove_startup', lambda: True)
    result = runner.invoke(cli, ['app', '--no-startup'])
    assert result.exit_code == 0
    assert '当前无已就绪实例' in result.output
    assert '仍在运行' not in result.output

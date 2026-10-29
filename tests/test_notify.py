"""L1 notification mode."""

from __future__ import annotations

from gloss import notify


def test_send_builds_valid_applescript(monkeypatch):
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(notify.subprocess, "run", fake_run)
    assert notify.send("gloss · QUIC", '已收录 · 说"引号"和 \\ 反斜杠') is True
    script = captured["args"][2]  # args = ["osascript", "-e", script]
    assert captured["args"][0] == "osascript"
    assert script.startswith("display notification ")
    assert 'title "gloss · QUIC"' in script
    assert '\\"' in script  # quotes escaped
    assert "\\\\" in script  # backslashes escaped


def test_send_survives_missing_osascript(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("osascript")

    monkeypatch.setattr(notify.subprocess, "run", boom)
    assert notify.send("t", "m") is False


def test_paste_notify_sends_notification(runner, home, fake_llm, monkeypatch):
    sent = []
    monkeypatch.setattr("gloss.notify.send", lambda t, m: sent.append((t, m)) or True)
    from gloss import clipboard

    monkeypatch.setattr(clipboard, "read_clipboard", lambda: ("text", "QUIC"))
    fake_llm.term_queue = [(
        {
            "term": "QUIC",
            "aliases": [],
            "one_liner": "基于 UDP 的传输协议",
            "detail": "低延迟。",
            "tags": ["network"],
            "related": [],
        },
        300,
    )]

    result = runner.invoke(cli_family(), ["paste", "--notify"])
    assert result.exit_code == 0, result.output
    assert len(sent) == 1
    assert "QUIC" in sent[0][0]
    assert "已收录" in sent[0][1]
    assert "已通知" in result.output


def test_paste_notify_cache_hit(runner, home, fake_llm, monkeypatch):
    from gloss import clipboard, db

    monkeypatch.setattr("gloss.notify.send", lambda t, m: True)
    monkeypatch.setattr(clipboard, "read_clipboard", lambda: ("text", "QUIC"))
    runner.invoke(cli_family(), ["paste"])  # ingest once silently
    sent = []
    monkeypatch.setattr("gloss.notify.send", lambda t, m: sent.append((t, m)) or True)
    runner.invoke(cli_family(), ["paste", "--notify"])
    assert "已在词库" in sent[0][1]
    assert db.get_card("QUIC").hit_count == 1


def cli_family():
    from gloss.cli import cli

    return cli

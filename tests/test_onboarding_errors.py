"""Onboarding and actionable LLM error guidance tests (G-P3-2)."""

from __future__ import annotations

import pytest

from gloss.cli import cli
from gloss.llm import classify_llm_error, get_client
from gloss.models import GlossError


def test_missing_api_key_guides_to_demo_and_setup(home, monkeypatch):
    monkeypatch.delenv("GLOSS_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(GlossError) as exc:
        get_client().explain_term("RAG", [])
    msg = str(exc.value)
    assert "还没有配置 API key" in msg
    assert "调用模型失败" not in msg
    assert "gloss demo" in msg
    assert "gloss setup" in msg
    assert "GLOSS_API_KEY" in msg
    assert "base_url" in msg


def test_classify_llm_error_includes_next_steps():
    msg_401 = classify_llm_error("Error code: 401 - invalid API key")
    assert "认证失败" in msg_401 and "gloss setup" in msg_401

    msg_404 = classify_llm_error("404 model_not_found: no such model x")
    assert "模型不存在" in msg_404 and "gloss config show" in msg_404

    msg_timeout = classify_llm_error("Request timed out after 60s")
    assert "连接超时" in msg_timeout and "llm.timeout" in msg_timeout

    msg_conn = classify_llm_error("connection refused by 127.0.0.1")
    assert "连不上服务地址" in msg_conn and "gloss setup" in msg_conn

    msg_429 = classify_llm_error("Error code: 429 - too many requests")
    assert "请求太频繁" in msg_429 and "429" not in msg_429

    msg_5xx = classify_llm_error("Error code: 503 - upstream unavailable")
    assert "服务端故障" in msg_5xx and "gloss ls" in msg_5xx


def test_fresh_home_empty_ls_and_three_command_offline_flow(runner, home, monkeypatch):
    monkeypatch.delenv("GLOSS_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    empty = runner.invoke(cli, ["ls"])
    assert empty.exit_code == 0, empty.output
    assert "gloss demo" in empty.output
    assert "gloss setup" in empty.output

    seeded = runner.invoke(cli, ["demo"])
    assert seeded.exit_code == 0, seeded.output
    assert "9 张示例卡片" in seeded.output

    listed = runner.invoke(cli, ["ls"])
    assert listed.exit_code == 0, listed.output
    assert "RAG" in listed.output

    card = runner.invoke(cli, ["RAG"])
    assert card.exit_code == 0, card.output
    assert "RAG" in card.output
    assert "cache hit" in card.output

"""L2 local server: API endpoints and the popup page."""

from __future__ import annotations

import http.client
import json
import sys

from gloss import db

from conftest import http_request as _request


def test_get_page_contains_input_and_prefill(server_url, monkeypatch):
    from gloss import clipboard

    monkeypatch.setattr(clipboard, "read_clipboard", lambda: ("text", "QUIC"))
    status, ctype, data = _request(server_url)
    assert status == 200
    assert "text/html" in ctype
    html = data.decode("utf-8")
    assert "<input" in html
    assert "QUIC" in html  # clipboard pre-filled


def test_glass_param_toggles_body_class(server_url):
    _, _, plain = _request(server_url)
    assert "<body>" in plain.decode("utf-8")

    conn = http.client.HTTPConnection(server_url, timeout=30)
    conn.request("GET", "/?glass=1")
    resp = conn.getresponse()
    glass_html = resp.read().decode("utf-8")
    conn.close()
    assert resp.status == 200  # 带查询参数也必须路由到页面
    assert '<body class="glass">' in glass_html


def test_get_page_with_image_clipboard_shows_hint(server_url, monkeypatch):
    from gloss import clipboard

    monkeypatch.setattr(clipboard, "read_clipboard", lambda: ("image", "/tmp/x.png"))
    _, _, data = _request(server_url)
    assert "图片" in data.decode("utf-8")


def test_lookup_new_term_returns_card(server_url):
    status, _, data = _request(server_url, "POST", {"text": "RAG"})
    assert status == 200
    payload = json.loads(data)
    assert payload["cache_hit"] is False
    assert payload["card"]["term"] == "RAG"
    assert payload["card"]["one_liner"]
    assert db.get_card("RAG") is not None


def test_lookup_again_is_cache_hit(server_url):
    _request(server_url, "POST", {"text": "RAG"})
    status, _, data = _request(server_url, "POST", {"text": "rag"})
    payload = json.loads(data)
    assert status == 200
    assert payload["cache_hit"] is True


def test_lookup_includes_linked_and_pending(server_url):
    from conftest import make_card

    make_card("Embedding", one_liner="向量化")
    status, _, data = _request(server_url, "POST", {"text": "RAG"})
    payload = json.loads(data)
    linked_terms = [x["term"] for x in payload["card"]["linked"]]
    assert "Embedding" in linked_terms
    assert "向量数据库" in payload["card"]["pending"]


def test_long_text_returns_snippet_with_key_terms(server_url):
    status, _, data = _request(server_url, "POST", {"text": "这是一段足够长的阅读内容。" * 20})
    payload = json.loads(data)
    assert status == 200
    assert payload["card"]["type"] == "snippet"
    assert payload["key_terms"]


def test_empty_text_is_400(server_url):
    status, _, data = _request(server_url, "POST", {"text": "  "})
    assert status == 400
    assert "error" in json.loads(data)


def test_bad_json_is_400(server_url):
    conn = http.client.HTTPConnection(server_url, timeout=30)
    conn.request("POST", "/api/lookup", body=b"not json", headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    assert resp.status == 400
    conn.close()


def test_unknown_path_is_404(server_url):
    status, _, _ = _request(server_url)
    conn = http.client.HTTPConnection(server_url, timeout=30)
    conn.request("GET", "/nope")
    assert conn.getresponse().status == 404
    conn.close()


def test_page_contains_auto_trigger_logic(server_url, monkeypatch):
    from gloss import clipboard

    monkeypatch.setattr(clipboard, "read_clipboard", lambda: ("text", "QUIC"))
    _, _, data = _request(server_url)
    html = data.decode("utf-8")
    assert "/api/image" in html
    assert "CLIP_KIND" in html
    assert "if (AUTO) lookup();" in html  # 自动模式：预填即查


def test_api_image_with_clipboard_image(server_url, fake_llm, monkeypatch):
    from gloss import clipboard

    monkeypatch.setattr(clipboard, "read_clipboard", lambda: ("image", "/tmp/shot.png"))
    status, _, data = _request(server_url, "POST", {"text": "ignored"}, path="/api/image")
    assert status == 200
    payload = json.loads(data)
    assert payload["card"]["type"] == "snippet"
    assert fake_llm.image_calls == ["/tmp/shot.png"]


def test_api_image_without_image_is_400(server_url, fake_llm, monkeypatch):
    from gloss import clipboard

    monkeypatch.setattr(clipboard, "read_clipboard", lambda: ("text", "just a word"))
    status, _, data = _request(server_url, "POST", {}, path="/api/image")
    assert status == 400
    assert "没有图片" in json.loads(data)["error"]


# -- 设置：读取 / 保存 / 连接测试 ----------------------------------------------


def test_config_get_masks_key_and_reports_defaults(server_url, home):
    from gloss.cli import cli as _  # noqa: F401  (确保导入面一致)

    status, _, data = _request(server_url, path="/api/config")
    assert status == 200
    cfg = json.loads(data)
    assert cfg["auto_lookup"] is True
    assert cfg["hotkey"] == ("cmd+g" if sys.platform == "darwin" else "ctrl+g")
    assert cfg["api_key"] == ""


def test_config_get_masks_saved_key(server_url, home):
    from gloss.config import set_config_value

    set_config_value("llm.api_key", "sk-very-secret-key-9999")
    status, _, data = _request(server_url, path="/api/config")
    payload = json.loads(data)
    assert payload["api_key"] == "sk-v…9999"
    assert payload["api_key"] != "sk-very-secret-key-9999"  # 明文不出网
    assert payload["has_api_key"] is True


def test_config_post_saves_settings(server_url, home):
    status, _, data = _request(
        server_url, "POST",
        {"base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat",
         "auto_lookup": False, "api_key": "sk-test-123456789"},
        path="/api/config",
    )
    assert status == 200
    assert json.loads(data)["ok"] is True

    from gloss.config import load_config

    cfg = load_config()
    assert cfg["llm"]["base_url"] == "https://api.deepseek.com/v1"
    assert cfg["llm"]["model"] == "deepseek-chat"
    assert cfg["app"]["auto_lookup"] is False
    assert cfg["llm"]["api_key"] == "sk-test-123456789"


def test_config_post_rejects_unknown_key(server_url, home):
    status, _, data = _request(server_url, "POST", {"nonsense": "x"}, path="/api/config")
    assert status == 400
    assert "不认识的设置项" in json.loads(data)["error"]


def test_config_test_without_key_is_friendly(server_url, home, monkeypatch):
    monkeypatch.delenv("GLOSS_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    status, _, data = _request(server_url, "POST", {}, path="/api/config/test")
    payload = json.loads(data)
    assert payload["ok"] is False
    assert "API key" in payload["error"]


def test_classify_llm_error_modes():
    from gloss.server import classify_llm_error

    assert "认证失败" in classify_llm_error("Error code: 401 - invalid API key")
    assert "模型不存在" in classify_llm_error("404 model_not_found: no such model x")
    assert "超时" in classify_llm_error("Request timed out after 60s")
    assert "连不上服务地址" in classify_llm_error("connection refused by 127.0.0.1")
    assert "测试失败" in classify_llm_error("weird provider panic")


# -- 弹窗页：两种查询模式 + 首次引导 --------------------------------------------


def test_page_injects_auto_lookup_default_true(server_url):
    _, _, data = _request(server_url)
    assert "const AUTO = true" in data.decode("utf-8")


def test_page_injects_auto_lookup_false_when_configured(server_url, home):
    from gloss.config import set_config_value

    set_config_value("app.auto_lookup", "false")
    _, _, data = _request(server_url)
    html = data.decode("utf-8")
    assert "const AUTO = false" in html
    assert "按回车确认查询" in html  # 手动确认模式的提示语在页面里


def test_page_injects_no_key_onboarding(server_url, home, monkeypatch):
    monkeypatch.delenv("GLOSS_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _, _, data = _request(server_url)
    html = data.decode("utf-8")
    assert "let NO_KEY = true" in html
    assert "导入示例词库" in html  # 首次引导：先体验离线示例
    assert "打开设置" in html


# -- 首次引导的离线示例 ---------------------------------------------------------


def test_api_demo_seeds_offline_cards(server_url, home):
    status, _, data = _request(server_url, "POST", {}, path="/api/demo")
    assert status == 200
    payload = json.loads(data)
    assert payload["added"] >= 9
    assert db.get_card("RAG") is not None

    status, _, data = _request(server_url, "POST", {}, path="/api/demo")
    assert json.loads(data)["skipped"] >= 9  # 重复导入不重复建卡


def test_import_demo_releases_busy_before_opening_card(server_url):
    """P2 回归：importDemo 里 busy 释放必须先于 glossNow，否则卡片永远打不开。"""
    _, _, data = _request(server_url)
    js = data.decode("utf-8")
    body = js[js.index("async function importDemo"):js.index("if (NO_KEY)")]
    assert body.index("busy = false;") < body.index('glossNow("RAG")')


def test_config_post_can_clear_vision_model(server_url, home):
    from gloss.config import load_config, set_config_value

    set_config_value("llm.vision_model", "gpt-4o")
    status, _, data = _request(server_url, "POST", {"vision_model": ""}, path="/api/config")
    assert status == 200
    assert load_config()["llm"]["vision_model"] == ""


def test_page_settings_ui_prefills_and_can_clear(server_url):
    _, _, data = _request(server_url)
    js = data.decode("utf-8")
    assert "dataset.pristine" in js      # 预填真实值，清空可被识别为修改
    assert "payload[k] = now;" in js     # 改动（含清空）才提交

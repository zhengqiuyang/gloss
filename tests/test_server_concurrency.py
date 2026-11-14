"""ThreadingHTTPServer 并发验收：慢 LLM 调用不再阻塞其他请求。

历史行为：HTTPServer 单线程，一次 LLM 调用（最长 60s）期间设置页/查询全卡死。
现在：每个请求独立线程；DB 用单连接 + RLock 串行化（db._serialized），
不存在每请求新建连接，也不出现「SQLite objects created in a thread」。
"""

import json
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import pytest

import gloss.db
from gloss.server import start_server


@pytest.fixture()
def server_url(home, fake_llm):
    srv = start_server(0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _post(base, path, payload):
    req = urllib.request.Request(
        f"http://{base}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, json.loads(resp.read())


def _get(base, path):
    with urllib.request.urlopen(f"http://{base}{path}", timeout=10) as resp:
        return resp.status, json.loads(resp.read())


def test_slow_lookup_does_not_block_config(server_url, fake_llm, monkeypatch):
    """mock 一个 sleep 2s 的 LLM：期间并发的缓存命中查询/设置页不等它。"""
    # 1) 先正常收录一张卡（走 FakeLLM，瞬间完成），建立缓存
    status, _ = _post(server_url, "/api/lookup", {"text": "RAG"})
    assert status == 200

    # 2) 把 LLM 换成 2s 慢速版，并补一个 NEWTerm 的 payload（队列已被预热消费）
    real_explain = fake_llm.explain_term

    def slow_explain(*args, **kwargs):
        time.sleep(2)
        return real_explain(*args, **kwargs)

    monkeypatch.setattr(fake_llm, "explain_term", slow_explain)
    fake_llm.term_queue.append((
        {"term": "NEWTerm", "aliases": [], "one_liner": "并发验证用",
         "detail": "并发验证详情", "tags": [], "related": []},
        100,
    ))

    # 3) 后台线程发起慢查询（cache miss → 会真正调 LLM）
    slow_result = {}

    def do_slow_lookup():
        try:
            slow_result["resp"] = _post(server_url, "/api/lookup", {"text": "NEWTerm"})
        except Exception as exc:  # noqa: BLE001
            body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
            slow_result["error"] = f"{exc} | body={body}"

    slow_thread = threading.Thread(target=do_slow_lookup, daemon=True)
    slow_thread.start()
    time.sleep(0.3)  # 确保慢查询已进入 2s 的 sleep

    # 4) 同时并发 10 个缓存命中查询（并发走 db.get_card + increment_hit）
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(lambda _: _post(server_url, "/api/lookup", {"text": "RAG"}),
                                range(10)))
        # 再捎带 10 个 /api/config（设置页，读 config.toml）
        configs = list(pool.map(lambda _: _get(server_url, "/api/config"), range(10)))
    elapsed = time.monotonic() - start

    assert elapsed < 1.0, f"并发请求总耗时 {elapsed:.2f}s —— 被慢 lookup 串行了？"
    for status, payload in results:
        assert status == 200
        assert payload["cache_hit"] is True
        assert "SQLite objects" not in json.dumps(payload)  # 跨线程错误绝不入响应
    for status, payload in configs:
        assert status == 200
        assert "model" in payload

    # 5) 慢查询最终也成功（~2s），期间没被并发写坏
    slow_thread.join(timeout=10)
    assert "error" not in slow_result, slow_result.get("error")
    status, payload = slow_result["resp"]
    assert status == 200
    assert payload["card"]["term"] == "NEWTerm"
    assert gloss.db.get_card("RAG") is not None  # 并发期间缓存命中计数没写坏库


def test_shutdown_releases_port(home):
    """优雅退出后端口立即可复用（allow_reuse_address + serve_forever 退出）。"""
    srv = start_server(0)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.2)
    srv.shutdown()
    thread.join(timeout=5)
    srv.server_close()

    srv2 = start_server(port)  # 端口立即重绑成功 = 已释放
    srv2.server_close()

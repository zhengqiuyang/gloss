"""Read-only MCP server tests: unit JSON-RPC dispatch and real stdio subprocess."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys

from conftest import make_card

from gloss import __version__, db, demo
from gloss.mcp import handle_message, serve_stdio


def _call_tool(name: str, arguments: dict | None = None, msg_id: int = 1) -> dict:
    params: dict = {"name": name}
    if arguments is not None:
        params["arguments"] = arguments
    resp = handle_message(
        {"jsonrpc": "2.0", "id": msg_id, "method": "tools/call", "params": params}
    )
    assert resp is not None
    return resp


def _tool_payload(resp: dict) -> dict:
    assert "result" in resp, resp
    result = resp["result"]
    assert result["isError"] is False
    return json.loads(result["content"][0]["text"])


def test_initialize_ping_and_notification():
    init = handle_message({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-03-26"},
    })
    assert init == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "gloss", "version": __version__},
        },
    }
    init_default = handle_message({"jsonrpc": "2.0", "id": 2, "method": "initialize"})
    assert init_default is not None
    assert init_default["result"]["protocolVersion"] == "2024-11-05"

    assert handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert handle_message({"jsonrpc": "2.0", "id": 3, "method": "ping"}) == {
        "jsonrpc": "2.0",
        "id": 3,
        "result": {},
    }


def test_tools_list_exposes_four_read_only_tools():
    resp = handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert resp is not None
    tools = resp["result"]["tools"]
    names = [t["name"] for t in tools]
    assert names == ["gloss_search", "gloss_get", "gloss_related", "gloss_recent"]
    for tool in tools:
        assert "description" in tool
        assert tool["inputSchema"]["type"] == "object"


def test_tools_search_get_related_recent(home):
    demo.seed_demo()
    # 额外加一张带未收录关联概念的卡片以同时覆盖 linked 和 pending
    card = make_card(
        "PagedAttention",
        aliases=["分页注意力"],
        one_liner="按页管理 KV Cache 显存的推理调度技术。",
        related_names=["RAG", "vLLM"],
    )
    db.resolve_related(card)

    # gloss_get: 精确匹配 + 别名匹配 + 未命中
    got = _tool_payload(_call_tool("gloss_get", {"term": "分页注意力"}))
    assert got["found"] is True
    assert got["card"]["term"] == "PagedAttention"
    assert "RAG" in got["card"]["linked"]
    assert "vLLM" in got["card"]["pending"]

    missed = _tool_payload(_call_tool("gloss_get", {"term": "NonExistentTerm"}))
    assert missed["found"] is False

    # gloss_search: 命中与 limit 限制
    found = _tool_payload(_call_tool("gloss_search", {"query": "向量", "limit": 2}))
    assert found["count"] <= 2
    assert any(c["term"] == "Embedding" or "向量" in c["term"] for c in found["cards"])

    # gloss_related: 命中与未命中
    rel = _tool_payload(_call_tool("gloss_related", {"term": "RAG"}))
    assert rel["found"] is True
    assert any(item["term"] == "Embedding" for item in rel["linked"])

    rel_miss = _tool_payload(_call_tool("gloss_related", {"term": "Unknown"}))
    assert rel_miss == {"found": False, "term": "Unknown", "linked": [], "pending": []}

    # gloss_recent: 最近 7 天 vs 最近 30 天
    recent_7 = _tool_payload(_call_tool("gloss_recent", {"days": 7}))
    recent_30 = _tool_payload(_call_tool("gloss_recent", {"days": 30, "limit": 20}))
    assert recent_30["count"] >= recent_7["count"]
    assert any(c["term"] == "PagedAttention" for c in recent_7["cards"])


def test_tool_and_protocol_error_handling(home):
    # 工具参数错误 -> isError: True
    bad_query = _call_tool("gloss_search", {"query": "   "})
    assert bad_query["result"]["isError"] is True
    bad_days = _call_tool("gloss_recent", {"days": True})
    assert bad_days["result"]["isError"] is True

    # 未知工具 / 缺失工具名 / 参数类型错 -> -32602
    unknown_tool = _call_tool("gloss_delete", {"term": "RAG"})
    assert unknown_tool["error"]["code"] == -32602
    missing_name = handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {}}
    )
    assert missing_name is not None and missing_name["error"]["code"] == -32602
    bad_args_type = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "gloss_get", "arguments": []},
        }
    )
    assert bad_args_type is not None and bad_args_type["error"]["code"] == -32602

    # 未知方法 -> -32601；无效请求 -> -32600
    unknown_method = handle_message({"jsonrpc": "2.0", "id": 9, "method": "resources/list"})
    assert unknown_method is not None and unknown_method["error"]["code"] == -32601
    bad_req = handle_message({"jsonrpc": "2.0", "id": 10})
    assert bad_req is not None and bad_req["error"]["code"] == -32600


def test_serve_stdio_handles_parse_errors_and_notifications(home):
    make_card("QUIC", one_liner="基于 UDP 的低延迟传输协议")
    inp = io.StringIO(
        "\n"
        "{not-valid-json\n"
        "[]\n"
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
        + "\n"
        + json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 42,
                "method": "tools/call",
                "params": {"name": "gloss_get", "arguments": {"term": "QUIC"}},
            }
        )
        + "\n"
    )
    out = io.StringIO()
    serve_stdio(in_stream=inp, out_stream=out)
    lines = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
    assert len(lines) == 3
    assert lines[0]["error"]["code"] == -32700
    assert lines[1]["error"]["code"] == -32600
    assert lines[2]["id"] == 42
    payload = json.loads(lines[2]["result"]["content"][0]["text"])
    assert payload["found"] is True
    assert payload["card"]["term"] == "QUIC"


def test_mcp_subprocess_roundtrip(tmp_path):
    env = {**os.environ, "GLOSS_HOME": str(tmp_path), "PYTHONIOENCODING": "utf-8"}
    seed_proc = subprocess.run(
        [sys.executable, "-m", "gloss", "demo"],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert seed_proc.returncode == 0, seed_proc.stderr

    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "gloss_get", "arguments": {"term": "RAG"}},
        },
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "gloss_search", "arguments": {"query": "向量"}},
        },
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "gloss_related", "arguments": {"term": "RAG"}},
        },
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "gloss_recent", "arguments": {"days": 30}},
        },
    ]
    stdin_payload = "\n".join(json.dumps(req, ensure_ascii=False) for req in requests) + "\n"

    proc = subprocess.run(
        [sys.executable, "-m", "gloss", "mcp"],
        input=stdin_payload,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""

    responses = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    assert [r["id"] for r in responses] == [1, 2, 3, 4, 5, 6]
    assert responses[0]["result"]["serverInfo"]["name"] == "gloss"
    assert len(responses[1]["result"]["tools"]) == 4

    rag_get = json.loads(responses[2]["result"]["content"][0]["text"])
    assert rag_get["found"] is True
    assert rag_get["card"]["term"] == "RAG"

    vec_search = json.loads(responses[3]["result"]["content"][0]["text"])
    assert vec_search["count"] >= 1

    rag_related = json.loads(responses[4]["result"]["content"][0]["text"])
    assert rag_related["found"] is True
    assert any(item["term"] == "Embedding" for item in rag_related["linked"])

    recent = json.loads(responses[5]["result"]["content"][0]["text"])
    assert recent["count"] == 9

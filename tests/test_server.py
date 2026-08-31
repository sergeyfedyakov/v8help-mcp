"""Интеграционные тесты MCP-сервера (FastMCP): in-memory, stdio e2e, HTTP."""

import asyncio
import json
import queue
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import uvicorn
from fastmcp import Client

from v8help.config import Config
from v8help.indexer import build_index
from v8help.server import build_server

TOOL_NAMES = {
    "search", "get_page", "hierarchy", "related", "build", "build_status",
    "discover", "config_get", "config_set",
}


def _build_tiny_index(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "lang__def_String.md").write_text(
        "# Строка\n\nТип Строка хранит текст.\n", encoding="utf-8"
    )
    (corpus / "Глобальный_контекст.СтрНайтиПоРегулярномуВыражению.md").write_text(
        "# Глобальный контекст.СтрНайтиПоРегулярномуВыражению\n\n"
        "Выполняет поиск строки по регулярному выражению.\n",
        encoding="utf-8",
    )
    (corpus / "objects__catalog1.md").write_text(
        "# Справочник\n\nСм. также [Строка](lang__def_String.md).\n",
        encoding="utf-8",
    )
    config = Config()
    config.corpus_dir = corpus
    config.db_path = tmp_path / "test.db"
    config.books = []
    build_index(config)
    return config


def _make_server(tmp_path, config_path=None):
    return build_server(_build_tiny_index(tmp_path), config_path)


def _run(coro):
    return asyncio.run(coro)


async def _call_ok(client, name, args=None):
    res = await client.call_tool(name, args or {}, raise_on_error=False)
    assert res.is_error is False, res.content[0].text
    return json.loads(res.content[0].text)


# ---------- in-memory (Client над build_server) ----------


def test_tools_list(tmp_path):
    async def main():
        async with Client(_make_server(tmp_path)) as client:
            names = {t.name for t in await client.list_tools()}
            assert names == TOOL_NAMES

    _run(main())


def test_initialize_server_info(tmp_path):
    async def main():
        async with Client(_make_server(tmp_path)) as client:
            info = client.server_info
            assert info.name == "v8help"

    _run(main())


def test_tool_discover(tmp_path):
    async def main():
        async with Client(_make_server(tmp_path)) as client:
            data = await _call_ok(client, "discover")
            assert data["index"]["exists"] is True
            assert data["index"]["pages"] == 3
            assert "bin_dir" in data
            assert "platforms" in data
            assert data["config"]["books"] == []

    _run(main())


def test_tool_search_split(tmp_path):
    async def main():
        async with Client(_make_server(tmp_path)) as client:
            data = await _call_ok(client, "search", {"query": "регулярному"})
            ids = [r["id"] for r in data["results"]]
            assert "Глобальный_контекст.СтрНайтиПоРегулярномуВыражению" in ids

    _run(main())


def test_tool_get_page(tmp_path):
    async def main():
        async with Client(_make_server(tmp_path)) as client:
            data = await _call_ok(client, "get_page", {"id": "lang__def_String"})
            assert data["section"] == "lang"
            assert "текст" in data["body"]
            assert data["total_chunks"] == 1
            assert data["truncated"] is False

    _run(main())


def test_tool_get_page_many(tmp_path):
    async def main():
        async with Client(_make_server(tmp_path)) as client:
            data = await _call_ok(
                client, "get_page",
                {"id": ["lang__def_String", "objects__catalog1", "нет_такой"]},
            )
            assert data["requested"] == 3
            assert data["returned"] == 2
            assert data["missing"] == ["нет_такой"]
            assert data["truncated"] is False
            names = [p["filename"] for p in data["pages"]]
            assert names == ["lang__def_String", "objects__catalog1"]
            assert data["pages"][0]["total_chunks"] == 1

    _run(main())


def test_tool_related(tmp_path):
    async def main():
        async with Client(_make_server(tmp_path)) as client:
            data = await _call_ok(client, "related", {"id": "objects__catalog1"})
            assert data["outgoing"] == ["lang__def_String"]

    _run(main())


def test_tool_get_page_missing(tmp_path):
    async def main():
        async with Client(_make_server(tmp_path)) as client:
            res = await client.call_tool(
                "get_page", {"id": "нет_такой"}, raise_on_error=False
            )
            assert res.is_error is True

    _run(main())


def test_tool_config_get(tmp_path):
    async def main():
        async with Client(_make_server(tmp_path)) as client:
            data = await _call_ok(client, "config_get")
            assert data["search"]["backend"] == "fts"
            assert "embedder" in data

    _run(main())


def test_tool_config_set_persists(tmp_path):
    config = _build_tiny_index(tmp_path)
    config_path = str(tmp_path / "v8help.toml")
    mcp = build_server(config, config_path)

    async def main():
        async with Client(mcp) as client:
            data = await _call_ok(
                client, "config_set",
                {"values": {"search.backend": "hybrid", "search.limit": 25}},
            )
            assert data["config"]["search"]["backend"] == "hybrid"
            assert data["config"]["search"]["limit"] == 25
            assert Path(config_path).exists()
            loaded = Config.load(config_path)
            assert loaded.search.backend == "hybrid"
            assert loaded.search.limit == 25

    _run(main())


def test_tool_config_set_bad_backend(tmp_path):
    async def main():
        async with Client(_make_server(tmp_path)) as client:
            res = await client.call_tool(
                "config_set", {"values": {"search.backend": "bogus"}},
                raise_on_error=False,
            )
            assert res.is_error is True

    _run(main())


def test_tool_config_set_unknown_key(tmp_path):
    async def main():
        async with Client(_make_server(tmp_path)) as client:
            res = await client.call_tool(
                "config_set", {"values": {"nope.key": 1}},
                raise_on_error=False,
            )
            assert res.is_error is True

    _run(main())


# ---------- stdio e2e (реальный процесс через subprocess) ----------


def _write_toml(tmp_path, config):
    cfg_path = tmp_path / "v8help.toml"
    cfg_path.write_text(
        f'corpus_dir = "{config.corpus_dir.as_posix()}"\n'
        f'db_path = "{config.db_path.as_posix()}"\n'
        f"books = []\n",
        encoding="utf-8",
    )
    return cfg_path


def _stdio_start(config_path):
    p = subprocess.Popen(
        [sys.executable, "-m", "v8help", "--config", str(config_path), "serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    q = queue.Queue()

    def reader():
        for raw in p.stdout:
            line = raw.decode("utf-8").strip()
            if line:
                try:
                    q.put(json.loads(line))
                except json.JSONDecodeError:
                    pass

    threading.Thread(target=reader, daemon=True).start()
    return p, q


def _stdio_send(p, msg):
    if isinstance(msg, str):
        p.stdin.write(msg.encode("utf-8"))
    else:
        p.stdin.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
    p.stdin.flush()


def _stdio_recv(q, expected_id, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            obj = q.get(timeout=0.25)
        except queue.Empty:
            continue
        if obj.get("id") == expected_id:
            return obj
    raise AssertionError(f"нет ответа для id={expected_id}")


def test_stdio_e2e(tmp_path):
    """Полный цикл по stdio: initialize, tools/list, tools/call, ошибки протокола."""
    config = _build_tiny_index(tmp_path)
    cfg_path = _write_toml(tmp_path, config)
    p, q = _stdio_start(cfg_path)
    try:
        _stdio_send(p, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        })
        resp = _stdio_recv(q, 1)
        assert resp["result"]["serverInfo"]["name"] == "v8help"
        assert resp["result"]["serverInfo"]["version"] == "0.10.0"

        _stdio_send(p, {"jsonrpc": "2.0", "method": "notifications/initialized"})

        _stdio_send(p, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        resp = _stdio_recv(q, 2)
        names = {t["name"] for t in resp["result"]["tools"]}
        assert names == TOOL_NAMES

        _stdio_send(p, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "search", "arguments": {"query": "регулярному"}},
        })
        resp = _stdio_recv(q, 3)
        data = json.loads(resp["result"]["content"][0]["text"])
        assert data["count"] >= 1

        # неизвестный метод -> JSON-RPC error -32601
        _stdio_send(p, {"jsonrpc": "2.0", "id": 7, "method": "nope", "params": {}})
        resp = _stdio_recv(q, 7)
        assert resp["error"]["code"] == -32601

        # неизвестный инструмент -> isError (не JSON-RPC error)
        _stdio_send(p, {
            "jsonrpc": "2.0", "id": 8, "method": "tools/call",
            "params": {"name": "nope", "arguments": {}},
        })
        resp = _stdio_recv(q, 8)
        assert resp["result"]["isError"] is True
    finally:
        p.stdin.close()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()


# ---------- HTTP (streamable-http) ----------


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port(port, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise AssertionError(f"порт {port} не поднялся")


def test_http_transport(tmp_path):
    """streamable-http: tools/list, tools/call, ошибка isError."""
    mcp = _make_server(tmp_path)
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(mcp.http_app(), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        _wait_port(port)

        async def main():
            async with Client(f"http://127.0.0.1:{port}/mcp") as client:
                names = {t.name for t in await client.list_tools()}
                assert names == TOOL_NAMES
                data = await _call_ok(client, "search", {"query": "регулярному"})
                assert data["count"] >= 1
                res = await client.call_tool(
                    "get_page", {"id": "нет_такой"}, raise_on_error=False
                )
                assert res.is_error is True

        _run(main())
    finally:
        server.should_exit = True
        thread.join(timeout=5)

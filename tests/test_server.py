import io
import json

from v8help.config import Config
from v8help.indexer import build_index
from v8help.server import McpServer, run


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


def _init_msg(iid):
    return {
        "jsonrpc": "2.0",
        "id": iid,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
    }


def test_initialize(tmp_path):
    server = McpServer(_build_tiny_index(tmp_path))
    resp = server.handle_message(_init_msg(1))
    assert resp["id"] == 1
    assert resp["result"]["protocolVersion"] == "2024-11-05"
    assert resp["result"]["serverInfo"]["name"] == "v8help"
    assert "tools" in resp["result"]["capabilities"]


def test_notification_returns_none(tmp_path):
    server = McpServer(_build_tiny_index(tmp_path))
    resp = server.handle_message(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}
    )
    assert resp is None


def test_tools_list(tmp_path):
    server = McpServer(_build_tiny_index(tmp_path))
    resp = server.handle_message(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    )
    names = {t["name"] for t in resp["result"]["tools"]}
    assert names == {
        "search", "get_page", "hierarchy", "related", "build", "build_status",
        "discover", "config_get", "config_set",
    }


def test_tool_discover(tmp_path):
    server = McpServer(_build_tiny_index(tmp_path))
    resp = _call(server, 20, "discover", {})
    data = _result_payload(resp)
    assert data["index"]["exists"] is True
    assert data["index"]["pages"] == 3
    assert "bin_dir" in data
    assert "platforms" in data
    assert data["config"]["books"] == []


def _call(server, iid, name, arguments=None):
    return server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": iid,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
    )


def _result_payload(resp):
    assert resp["result"]["isError"] is False, resp
    return json.loads(resp["result"]["content"][0]["text"])


def test_tool_search_split(tmp_path):
    server = McpServer(_build_tiny_index(tmp_path))
    resp = _call(server, 3, "search", {"query": "регулярному"})
    data = _result_payload(resp)
    ids = [r["id"] for r in data["results"]]
    assert "Глобальный_контекст.СтрНайтиПоРегулярномуВыражению" in ids


def test_tool_get_page(tmp_path):
    server = McpServer(_build_tiny_index(tmp_path))
    resp = _call(server, 4, "get_page", {"id": "lang__def_String"})
    data = _result_payload(resp)
    assert data["section"] == "lang"
    assert "текст" in data["body"]


def test_tool_related(tmp_path):
    server = McpServer(_build_tiny_index(tmp_path))
    resp = _call(server, 5, "related", {"id": "objects__catalog1"})
    data = _result_payload(resp)
    assert data["outgoing"] == ["lang__def_String"]


def test_tool_get_page_missing(tmp_path):
    server = McpServer(_build_tiny_index(tmp_path))
    resp = _call(server, 6, "get_page", {"id": "нет_такой"})
    assert resp["result"]["isError"] is True


def test_tool_config_get(tmp_path):
    server = McpServer(_build_tiny_index(tmp_path))
    resp = _call(server, 40, "config_get", {})
    data = _result_payload(resp)
    assert data["search"]["backend"] == "fts"
    assert "embedder" in data


def test_tool_config_set_persists(tmp_path):
    config = _build_tiny_index(tmp_path)
    config_path = str(tmp_path / "v8help.toml")
    server = McpServer(config, config_path=config_path)
    resp = _call(
        server, 41, "config_set",
        {"values": {"search.backend": "hybrid", "search.limit": 25}},
    )
    data = _result_payload(resp)
    assert data["config"]["search"]["backend"] == "hybrid"
    assert data["config"]["search"]["limit"] == 25
    from pathlib import Path

    assert Path(config_path).exists()
    loaded = Config.load(config_path)
    assert loaded.search.backend == "hybrid"
    assert loaded.search.limit == 25


def test_tool_config_set_bad_backend(tmp_path):
    server = McpServer(_build_tiny_index(tmp_path))
    resp = _call(server, 42, "config_set", {"values": {"search.backend": "bogus"}})
    assert resp["result"]["isError"] is True


def test_tool_config_set_unknown_key(tmp_path):
    server = McpServer(_build_tiny_index(tmp_path))
    resp = _call(server, 43, "config_set", {"values": {"nope.key": 1}})
    assert resp["result"]["isError"] is True


def test_unknown_method(tmp_path):
    server = McpServer(_build_tiny_index(tmp_path))
    resp = server.handle_message(
        {"jsonrpc": "2.0", "id": 7, "method": "nope", "params": {}}
    )
    assert resp["error"]["code"] == -32601


def test_unknown_tool(tmp_path):
    server = McpServer(_build_tiny_index(tmp_path))
    resp = _call(server, 8, "nope")
    assert resp["error"]["code"] == -32602


def test_run_handshake(tmp_path):
    config = _build_tiny_index(tmp_path)
    stdin = io.StringIO(
        json.dumps(_init_msg(1)) + "\n"
        + json.dumps(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )
        + "\n"
        + "not json\n"
    )
    out = io.StringIO()
    rc = run(config, stdin=stdin, stdout=out)
    assert rc == 0
    lines = [json.loads(l) for l in out.getvalue().splitlines()]
    assert [l["id"] for l in lines] == [1, 2, None]
    assert lines[-1]["error"]["code"] == -32700

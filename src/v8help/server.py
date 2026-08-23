"""MCP-сервер (stdio, JSON-RPC 2.0) поверх готовых модулей.

Протокол MCP поверх транспорта stdio = newline-delimited JSON-RPC 2.0:
по одному JSON-сообщению на строку, без вложенных переводов строк.

Инструменты: search / get_page / hierarchy / related / build / build_status.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from typing import Any, TextIO

from v8help import __version__
from v8help.config import Config, discover_platforms
from v8help.db import Database
from v8help.jobs import get_manager
from v8help.search.fts import FtsBackend

SERVER_NAME = "v8help"
PROTOCOL_VERSION = "2024-11-05"

# Корень проекта (для editable-установки: src/v8help/server.py -> parents[2]).
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resolve_paths(config: Config, base: Path) -> Config:
    if not config.corpus_dir.is_absolute():
        config.corpus_dir = base / config.corpus_dir
    if not config.db_path.is_absolute():
        config.db_path = base / config.db_path
    return config

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search",
        "description": (
            "Полнотекстовый поиск по справке 1С (FTS5). Возвращает список страниц "
            "с релевантностью (score) и сниппетами."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Поисковый запрос"},
                "section": {
                    "type": "string",
                    "description": "Фильтр по разделу: objects/tables/lang/query/clang",
                },
                "kind": {
                    "type": "string",
                    "description": "Фильтр по kind: page/member/index",
                },
                "limit": {"type": "integer", "description": "Максимум результатов"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_page",
        "description": (
            "Полный текст страницы справки по идентификатору (filename без .md "
            "или числовой id)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Идентификатор страницы"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "hierarchy",
        "description": (
            "Оглавление: без section — сводка по разделам; с section — группы "
            "страниц раздела (top-level объекты) с количеством."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "section": {"type": "string", "description": "Раздел для детализации"},
            },
        },
    },
    {
        "name": "related",
        "description": "Связанные страницы (исходящие и входящие ссылки) по id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Идентификатор страницы"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "build",
        "description": (
            "Пересобрать индекс: распаковка .hbk -> консолидация md-корпуса -> "
            "индексация FTS. Выполняется асинхронно: возвращает job_id сразу, "
            "результат и прогресс — через build_status. Может занять минуты."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Источники для сборки (по умолчанию все из конфига)",
                },
                "lang": {
                    "type": "string",
                    "description": "Язык: ru/en (по умолчанию из конфига)",
                },
                "cleanup": {
                    "type": "boolean",
                    "description": "Удалить corpus после индексации",
                },
                "force": {
                    "type": "boolean",
                    "description": "Пересобрать даже если индекс актуален",
                },
            },
        },
    },
    {
        "name": "build_status",
        "description": "Статус асинхронной сборки по job_id (running/done/error + прогресс).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Идентификатор job из build"},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "discover",
        "description": (
            "Показать конфиг и автодискавери: каталог bin установленной платформы "
            "1С (реестр Uninstall/ФС) и состояние индекса. В перспективе — доступные "
            "LLM на localhost-портах."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _lookup(conn, identifier: Any):
    ident = str(identifier)
    if ident.isdigit():
        return conn.execute(
            "SELECT * FROM pages WHERE id=?", (int(ident),)
        ).fetchone()
    return conn.execute(
        "SELECT * FROM pages WHERE filename=?", (ident,)
    ).fetchone()


def _json_rpc_result(msg_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _json_rpc_error(msg_id: Any, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": code, "message": message},
    }


class McpServer:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.db_path = Path(config.db_path)

    def handle_message(self, msg: dict) -> dict | None:
        """Возвращает JSON-RPC ответ (dict) для записи, либо None для нотификаций."""
        method = msg.get("method")
        msg_id = msg.get("id")
        if method is None:
            return _json_rpc_error(msg_id, -32600, "Invalid Request")
        if "id" not in msg:
            return None

        if method == "initialize":
            return _json_rpc_result(
                msg_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": __version__},
                },
            )
        if method == "ping":
            return _json_rpc_result(msg_id, {})
        if method == "tools/list":
            return _json_rpc_result(msg_id, {"tools": TOOLS})
        if method == "tools/call":
            return self._handle_tool_call(msg_id, msg.get("params") or {})
        if method == "resources/list":
            return _json_rpc_result(msg_id, {"resources": []})
        if method == "prompts/list":
            return _json_rpc_result(msg_id, {"prompts": []})
        return _json_rpc_error(msg_id, -32601, f"Method not found: {method}")

    def _handle_tool_call(self, msg_id: Any, params: dict) -> dict:
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            arguments = {}
        handler = _TOOL_HANDLERS.get(name)
        if handler is None:
            return _json_rpc_error(msg_id, -32602, f"Unknown tool: {name}")
        try:
            data = handler(self, arguments)
            return _json_rpc_result(
                msg_id,
                {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(data, ensure_ascii=False, indent=2),
                        }
                    ],
                    "isError": False,
                },
            )
        except Exception as exc:
            return _json_rpc_result(
                msg_id,
                {
                    "content": [{"type": "text", "text": f"Ошибка: {exc}"}],
                    "isError": True,
                },
            )

    # --- инструменты ---

    def _tool_search(self, args: dict) -> dict:
        backend = FtsBackend(self.db_path)
        limit = int(args.get("limit") or self.config.search.limit)
        results = backend.search(
            str(args["query"]),
            limit=limit,
            section=args.get("section"),
            kind=args.get("kind"),
        )
        return {
            "count": len(results),
            "results": [dataclasses.asdict(r) for r in results],
        }

    def _tool_get_page(self, args: dict) -> dict:
        db = Database(self.db_path)
        conn = db.connect()
        try:
            row = _lookup(conn, args["id"])
            if row is None:
                raise KeyError(f"Страница не найдена: {args['id']}")
            return {
                "id": row["id"],
                "filename": row["filename"],
                "title": row["title"],
                "section": row["section"],
                "kind": row["kind"],
                "hbk_source": row["hbk_source"],
                "body": row["body"],
            }
        finally:
            conn.close()

    def _tool_hierarchy(self, args: dict) -> dict:
        db = Database(self.db_path)
        conn = db.connect()
        try:
            section = args.get("section")
            if section:
                rows = conn.execute(
                    "SELECT filename FROM pages WHERE section=? ORDER BY filename",
                    (section,),
                ).fetchall()
                groups: dict[str, int] = {}
                for r in rows:
                    stem = r["filename"][:-3] if r["filename"].endswith(".md") else r["filename"]
                    top = stem.split(".", 1)[0]
                    groups[top] = groups.get(top, 0) + 1
                return {
                    "section": section,
                    "groups": [
                        {"name": k, "count": v} for k, v in sorted(groups.items())
                    ],
                }
            rows = conn.execute(
                "SELECT section, COUNT(*) AS n FROM pages GROUP BY section ORDER BY section"
            ).fetchall()
            return {
                "sections": [
                    {"section": r["section"], "count": r["n"]} for r in rows
                ]
            }
        finally:
            conn.close()

    def _tool_related(self, args: dict) -> dict:
        db = Database(self.db_path)
        conn = db.connect()
        try:
            row = _lookup(conn, args["id"])
            if row is None:
                raise KeyError(f"Страница не найдена: {args['id']}")
            fname = row["filename"]
            out = [
                r["dst"]
                for r in conn.execute(
                    "SELECT dst FROM links WHERE src=? ORDER BY dst", (fname,)
                )
            ]
            inc = [
                r["src"]
                for r in conn.execute(
                    "SELECT src FROM links WHERE dst=? ORDER BY src", (fname,)
                )
            ]
            return {
                "id": row["filename"],
                "title": row["title"],
                "outgoing": out,
                "incoming": inc,
            }
        finally:
            conn.close()

    def _tool_build(self, args: dict) -> dict:
        cfg = dataclasses.replace(self.config)
        ids = args.get("sources")
        if ids:
            idset = {str(x) for x in ids}
            if cfg.sources:
                cfg.sources = [s for s in cfg.sources if s.id in idset]
            else:
                cfg.books = [b for b in cfg.books if b in idset]
        if args.get("lang"):
            cfg.lang = str(args["lang"])
        force = bool(args.get("force", False))
        cleanup = args.get("cleanup")
        cleanup = bool(cleanup) if cleanup is not None else None
        bin_dir = ""
        if not cfg.sources:
            bin_dir = str(cfg.resolve_bin_dir() or "")
            if bin_dir in ("", "."):
                raise RuntimeError(
                    "bin_dir не задан и не найден автоматически. Укажите bin_dir в "
                    "конфиге или проверьте установку платформы 1С."
                )
        job = get_manager().start(cfg, force=force, cleanup=cleanup)
        return {"job_id": job.id, "status": "started", "bin_dir": bin_dir}

    def _tool_build_status(self, args: dict) -> dict:
        job = get_manager().status(str(args["job_id"]))
        if job is None:
            raise KeyError(f"Job не найден: {args['job_id']}")
        return job.as_dict()

    def _tool_discover(self, args: dict) -> dict:
        db = Database(self.db_path)
        index: dict = {"exists": db.exists()}
        if db.exists():
            conn = db.connect()
            try:
                meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
                pages = conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
                links = conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
            finally:
                conn.close()
            index.update({"pages": pages, "links": links, "meta": meta})
        bd = self.config.resolve_bin_dir()
        bin_dir = str(bd) if str(bd) not in ("", ".") else ""
        return {
            "bin_dir": bin_dir,
            "bin_dir_explicit": str(self.config.bin_dir) not in ("", "."),
            "platforms": discover_platforms(),
            "config": {
                "db_path": str(self.config.db_path),
                "corpus_dir": str(self.config.corpus_dir),
                "books": self.config.books,
                "lang": self.config.lang,
                "search": dataclasses.asdict(self.config.search),
            },
            "index": index,
        }


_TOOL_HANDLERS = {
    "search": McpServer._tool_search,
    "get_page": McpServer._tool_get_page,
    "hierarchy": McpServer._tool_hierarchy,
    "related": McpServer._tool_related,
    "build": McpServer._tool_build,
    "build_status": McpServer._tool_build_status,
    "discover": McpServer._tool_discover,
}


def _write(stdout: TextIO, obj: dict) -> None:
    stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    stdout.flush()


def _reconfigure_utf8(stream: TextIO) -> None:
    try:
        stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError, OSError):
        pass


def run(
    config: Config,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    if stdin is None:
        _reconfigure_utf8(sys.stdin)
        stdin = sys.stdin
    if stdout is None:
        _reconfigure_utf8(sys.stdout)
        stdout = sys.stdout
    _reconfigure_utf8(sys.stderr)
    server = McpServer(config)
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _write(stdout, _json_rpc_error(None, -32700, "Parse error"))
            continue
        try:
            resp = server.handle_message(msg)
        except Exception as exc:
            resp = _json_rpc_error(msg.get("id"), -32603, str(exc))
        if resp is not None:
            _write(stdout, resp)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    config_path = None
    if "--config" in argv:
        i = argv.index("--config")
        if i + 1 < len(argv):
            config_path = argv[i + 1]
    config: Config | None = None
    if config_path:
        try:
            config = Config.load(config_path)
        except FileNotFoundError:
            print(f"[v8help] config не найден: {config_path} — использую defaults", file=sys.stderr)
    if config is None:
        config = Config()
    base = Path(config_path).resolve().parent if config_path else PROJECT_ROOT
    return run(_resolve_paths(config, base))


if __name__ == "__main__":
    sys.exit(main())

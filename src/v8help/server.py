"""MCP-сервер на FastMCP: транспорты stdio и streamable-http.

Заменил ручной JSON-RPC (stdio-only) на FastMCP: инструменты регистрируются
декоратором ``@mcp.tool()``, транспорт выбирается при запуске
(``v8help-mcp`` / ``v8help serve`` — stdio, ``v8help serve --http`` —
streamable-http на ``/mcp``).
"""

from __future__ import annotations

import dataclasses
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from v8help import __version__
from v8help.config import Config, config_to_toml, discover_embedders, discover_platforms
from v8help.db import Database
from v8help.jobs import get_manager
from v8help.search import make_backend
from v8help.search.embedder import EmbedderError
from v8help.search.fts import FtsBackend

SERVER_NAME = "v8help"

# Статьи длиннее этого порога целиком не выдаются: только список чанков и первый чанк.
_PAGE_CHARS_LIMIT = 4000

# Корень проекта. Корректен при editable-установке (src/v8help/server.py ->
# parents[2]); для wheel-установки конфиг задаётся явно (--config / V8HELP_CONFIG).
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resolve_paths(config: Config, base: Path) -> Config:
    if not config.corpus_dir.is_absolute():
        config.corpus_dir = base / config.corpus_dir
    if not config.db_path.is_absolute():
        config.db_path = base / config.db_path
    return config


def _lookup(conn, identifier: Any) -> sqlite3.Row | None:
    ident = str(identifier)
    if ident.isdigit():
        return conn.execute(
            "SELECT * FROM pages WHERE id=?", (int(ident),)
        ).fetchone()
    return conn.execute(
        "SELECT * FROM pages WHERE filename=?", (ident,)
    ).fetchone()


def _section_groups(conn, section: str) -> dict:
    """Группы страниц раздела: топ-префикс filename (до первой точки) → число."""
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
        "groups": [{"name": k, "count": v} for k, v in sorted(groups.items())],
    }


def _index_stats(db: Database) -> dict:
    """Счётчики pages/links/chunks/vectors + meta из собранной БД."""
    conn = db.connect()
    try:
        def count(table: str) -> int:
            has = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] if has else 0

        return {
            "pages": count("pages"),
            "links": count("links"),
            "chunks": count("chunks"),
            "vectors": count("vectors"),
            "meta": dict(conn.execute("SELECT key, value FROM meta").fetchall()),
        }
    finally:
        conn.close()


def _require_bin_dir(cfg: Config) -> str:
    """Строка bin_dir для build без sources; ошибка, если он не найден."""
    bin_dir = str(cfg.resolve_bin_dir() or "")
    if bin_dir in ("", "."):
        raise RuntimeError(
            "bin_dir не задан и не найден автоматически. Укажите bin_dir в "
            "конфиге или проверьте установку платформы 1С."
        )
    return bin_dir


class _Tools:
    """Обработчики инструментов MCP (закрыты поверх готовой БД)."""

    def __init__(self, config: Config, config_path: str | None = None) -> None:
        self.config = config
        self.config_path = config_path
        self.db_path = Path(config.db_path)

    # --- search ---

    def search(self, query, section=None, kind=None, limit=None) -> dict:
        limit = int(limit or self.config.search.limit)
        backend = make_backend(self.config, self.db_path)
        name = (self.config.search.backend or "fts").lower()
        try:
            results = backend.search(query, limit=limit, section=section, kind=kind)
            used = name
        except (EmbedderError, sqlite3.OperationalError):
            # Нет эмбеддера/векторов — деградируем на FTS.
            results = FtsBackend(self.db_path).search(
                query, limit=limit, section=section, kind=kind
            )
            used = "fts (fallback)"
        return {
            "count": len(results),
            "backend": used,
            "results": [dataclasses.asdict(r) for r in results],
        }

    # --- get_page ---

    def get_page(self, ident, chunk=None, max_chars: int = 4000) -> dict:
        max_chars = int(max_chars or 4000)
        chunk_index = chunk
        if isinstance(ident, list):
            return self._get_pages_many(ident, max_chars)
        if isinstance(ident, str):
            return self._get_page_one(ident, chunk_index)
        raise ValueError("id должен быть строкой или массивом строк")

    @staticmethod
    def _legacy_page(page: sqlite3.Row) -> dict:
        """Старая БД без чанков — отдать страницу целиком, как раньше."""
        return {
            "id": page["id"], "filename": page["filename"], "title": page["title"],
            "section": page["section"], "kind": page["kind"],
            "hbk_source": page["hbk_source"], "body": page["body"],
        }

    @staticmethod
    def _page_chunks(conn, page_id: int) -> list[sqlite3.Row] | None:
        """Список чанков страницы (без тел) или None, если таблицы нет."""
        has_chunks = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chunks'"
        ).fetchone()
        if not has_chunks:
            return None
        return conn.execute(
            "SELECT id, chunk_index, chars FROM chunks"
            " WHERE page_id=? ORDER BY chunk_index",
            (page_id,),
        ).fetchall()

    @staticmethod
    def _chunk_body(conn, chunk_id: int) -> str:
        return conn.execute(
            "SELECT body FROM chunks WHERE id=?", (chunk_id,)
        ).fetchone()["body"]

    def _pick_chunk_body(self, conn, page, chunks, chunk_index) -> tuple[str, bool]:
        """(body, truncated) для запрошенного чанка или для начала длинной статьи."""
        total = len(chunks)
        if chunk_index is not None:
            row = next((c for c in chunks if c["chunk_index"] == int(chunk_index)), None)
            if row is None:
                raise ValueError(f"Чанк {chunk_index} не найден (всего {total})")
            return self._chunk_body(conn, row["id"]), total > 1
        body = page["body"]
        truncated = total > 1 or len(body) > _PAGE_CHARS_LIMIT
        if truncated:
            body = self._chunk_body(conn, chunks[0]["id"])
            truncated = total > 1 or len(page["body"]) > _PAGE_CHARS_LIMIT
        return body, truncated

    @staticmethod
    def _page_dict(page, chunks, body, truncated, chunk_index) -> dict:
        return {
            "id": page["id"],
            "filename": page["filename"],
            "title": page["title"],
            "section": page["section"],
            "kind": page["kind"],
            "hbk_source": page["hbk_source"],
            "chars": len(page["body"]),
            "chunk_index": chunk_index if chunk_index is not None else 0,
            "total_chunks": len(chunks),
            "truncated": truncated,
            "chunks": [
                {"index": c["chunk_index"], "chars": c["chars"]} for c in chunks
            ],
            "body": body,
        }

    def _get_page_one(self, ident: str, chunk_index=None) -> dict:
        db = Database(self.db_path)
        conn = db.connect()
        try:
            page = _lookup(conn, ident)
            if page is None:
                raise ValueError(f"Страница не найдена: {ident}")
            chunks = self._page_chunks(conn, page["id"])
            if not chunks:
                return self._legacy_page(page)
            body, truncated = self._pick_chunk_body(conn, page, chunks, chunk_index)
            return self._page_dict(page, chunks, body, truncated, chunk_index)
        finally:
            conn.close()

    def _get_pages_many(self, ids: list[str], max_chars: int) -> dict:
        pages = []
        missing: list[str] = []
        total_chars = 0
        for ident in ids:
            if total_chars >= max_chars:
                break
            try:
                page = self._get_page_one(str(ident))
            except ValueError:
                missing.append(str(ident))
                continue
            total_chars += len(page["body"])
            if total_chars > max_chars:
                break
            pages.append(page)
        return {
            "requested": len(ids),
            "returned": len(pages),
            "missing": missing,
            "total_chars": total_chars,
            "truncated": len(pages) < len(ids) - len(missing),
            "pages": pages,
        }

    # --- hierarchy / related ---

    def hierarchy(self, section=None) -> dict:
        db = Database(self.db_path)
        conn = db.connect()
        try:
            if section:
                return _section_groups(conn, section)
            rows = conn.execute(
                "SELECT section, COUNT(*) AS n FROM pages GROUP BY section ORDER BY section"
            ).fetchall()
            return {
                "sections": [{"section": r["section"], "count": r["n"]} for r in rows]
            }
        finally:
            conn.close()

    def related(self, id) -> dict:
        db = Database(self.db_path)
        conn = db.connect()
        try:
            row = _lookup(conn, id)
            if row is None:
                raise ValueError(f"Страница не найдена: {id}")
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

    # --- build / build_status ---

    @staticmethod
    def _narrow_sources(cfg: Config, idset: set[str]) -> None:
        if cfg.sources:
            cfg.sources = [s for s in cfg.sources if s.id in idset]
        else:
            cfg.books = [b for b in cfg.books if b in idset]

    def build(self, sources=None, lang=None, cleanup=None, force=False,
              chunk_size=None, chunk_overlap=None) -> dict:
        cfg = dataclasses.replace(self.config)
        if sources:
            self._narrow_sources(cfg, {str(x) for x in sources})
        if lang:
            cfg.lang = str(lang)
        if chunk_size is not None:
            cfg.build.chunk_size = int(chunk_size)
        if chunk_overlap is not None:
            cfg.build.chunk_overlap = int(chunk_overlap)
        force = bool(force)
        cleanup = bool(cleanup) if cleanup is not None else None
        bin_dir = ""
        if not cfg.sources:
            bin_dir = _require_bin_dir(cfg)
        job = get_manager().start(cfg, force=force, cleanup=cleanup)
        return {"job_id": job.id, "status": "started", "bin_dir": bin_dir}

    def build_status(self, job_id: str) -> dict:
        job = get_manager().status(str(job_id))
        if job is None:
            raise ValueError(f"Job не найден: {job_id}")
        return job.as_dict()

    # --- discover / config ---

    def discover(self) -> dict:
        db = Database(self.db_path)
        index: dict = {"exists": db.exists()}
        if db.exists():
            index.update(_index_stats(db))
        bd = self.config.resolve_bin_dir()
        return {
            "bin_dir": str(bd) if str(bd) not in ("", ".") else "",
            "bin_dir_explicit": str(self.config.bin_dir) not in ("", "."),
            "platforms": discover_platforms(),
            "embedders": discover_embedders(),
            "config": self.config.to_dict(),
            "index": index,
        }

    def config_get(self) -> dict:
        return self.config.to_dict()

    def config_set(self, values: dict) -> dict:
        if not isinstance(values, dict):
            raise ValueError("values должен быть объектом key=value")
        for key, value in values.items():
            _apply_config_value(self.config, key, value)
        self._persist_config()
        return {"config": self.config.to_dict()}

    def _persist_config(self) -> None:
        if not self.config_path:
            return
        path = Path(self.config_path)
        text = config_to_toml(self.config.to_dict())
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)


def _coerce_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "on")
    return bool(v)


def _set_search_value(config: Config, name: str, value, key: str) -> None:
    if name == "backend":
        v = str(value).lower()
        if v not in ("fts", "hybrid", "vectors"):
            raise ValueError(f"search.backend: fts|hybrid|vectors, получено {value!r}")
        config.search.backend = v
    elif name == "limit":
        config.search.limit = int(value)
    elif name == "max_chunks_per_page":
        config.search.max_chunks_per_page = int(value)
    else:
        raise KeyError(key)


def _set_build_value(config: Config, name: str, value, key: str) -> None:
    if name == "cleanup":
        config.build.cleanup = _coerce_bool(value)
    elif name == "chunk_size":
        config.build.chunk_size = int(value)
    elif name == "chunk_overlap":
        config.build.chunk_overlap = int(value)
    else:
        raise KeyError(key)


def _set_embedder_value(config: Config, branch: str, field: str, value,
                        key: str) -> None:
    target = config.embedder_index if branch == "index" else config.embedder_query
    if field in ("dims", "batch_size", "embed_chars", "threads"):
        setattr(target, field, int(value))
    elif field in ("model", "base_url", "api_key", "provider"):
        setattr(target, field, str(value))
    else:
        raise KeyError(key)


def _apply_config_value(config: Config, key: str, value) -> None:
    """Применяет одно плоское значение к Config (для config_set)."""
    parts = str(key).split(".")
    head = parts[0]
    if head == "search" and len(parts) == 2:
        _set_search_value(config, parts[1], value, key)
    elif head == "build" and len(parts) == 2:
        _set_build_value(config, parts[1], value, key)
    elif head == "bin_dir":
        config.bin_dir = Path(str(value))
    elif head == "lang":
        config.lang = str(value)
    elif head == "books":
        config.books = [str(x) for x in value]
    elif head == "embedder" and len(parts) == 3 and parts[1] in ("index", "query"):
        _set_embedder_value(config, parts[1], parts[2], value, key)
    else:
        raise KeyError(key)


def build_server(config: Config, config_path: str | None = None) -> FastMCP:
    """Собирает FastMCP-сервер с 9 инструментами поверх готового Config."""
    tools = _Tools(config, config_path)
    mcp = FastMCP(SERVER_NAME, version=__version__)

    @mcp.tool()
    def search(
        query: str,
        section: str | None = None,
        kind: str | None = None,
        limit: int | None = None,
    ) -> dict:
        """Полнотекстовый поиск по справке 1С (FTS5).

        Возвращает список страниц с релевантностью (score) и сниппетами.

        Args:
            query: Поисковый запрос.
            section: Фильтр по разделу: objects/tables/lang/query/clang.
            kind: Фильтр по kind: page/member/index.
            limit: Максимум результатов.
        """
        return tools.search(query, section=section, kind=kind, limit=limit)

    @mcp.tool()
    def get_page(
        id: str | list[str],
        chunk: int | None = None,
        max_chars: int | None = None,
    ) -> dict:
        """Полный текст страницы справки по идентификатору (filename без .md или числовой id).

        id может быть строкой (одна страница) или массивом строк (несколько страниц
        одним вызовом, 2-10 статей, пока суммарно не превышено max_chars). Длинные
        статьи (>4000 символов) целиком НЕ возвращаются: отдаётся список чанков и
        первый чанк; конкретный чанк читается через chunk=N.

        Args:
            id: Страница или список страниц.
            chunk: Номер чанка (0-based) для чтения части длинной статьи.
            max_chars: Лимит суммарного размера ответа (для массива id).
        """
        return tools.get_page(id, chunk=chunk, max_chars=max_chars)

    @mcp.tool()
    def hierarchy(section: str | None = None) -> dict:
        """Оглавление: без section — сводка по разделам; с section — группы страниц раздела.

        Args:
            section: Раздел для детализации (objects/tables/lang/query/clang).
        """
        return tools.hierarchy(section=section)

    @mcp.tool()
    def related(id: str) -> dict:
        """Связанные страницы (исходящие и входящие ссылки) по id.

        Args:
            id: Идентификатор страницы (filename или числовой id).
        """
        return tools.related(id)

    @mcp.tool()
    def build(
        sources: list[str] | None = None,
        lang: str | None = None,
        cleanup: bool | None = None,
        force: bool | None = None,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ) -> dict:
        """Пересобрать индекс: распаковка .hbk -> консолидация md-корпуса -> индексация.

        Выполняется асинхронно: возвращает job_id сразу, результат и прогресс — через
        build_status. Может занять минуты.

        Args:
            sources: Источники для сборки (по умолчанию все из конфига).
            lang: Язык: ru/en (по умолчанию из конфига).
            cleanup: Удалить corpus после индексации.
            force: Пересобрать даже если индекс актуален.
            chunk_size: Целевой размер чанка в символах (по умолчанию 1500).
            chunk_overlap: Перекрытие соседних чанков в символах (по умолчанию 200).
        """
        return tools.build(
            sources=sources, lang=lang, cleanup=cleanup, force=force,
            chunk_size=chunk_size, chunk_overlap=chunk_overlap,
        )

    @mcp.tool()
    def build_status(job_id: str) -> dict:
        """Статус асинхронной сборки по job_id (running/done/error + прогресс).

        Args:
            job_id: Идентификатор job из build.
        """
        return tools.build_status(job_id)

    @mcp.tool()
    def discover() -> dict:
        """Показать конфиг и автодискавери.

        Каталог bin установленной платформы 1С (реестр Uninstall/ФС), доступные
        эмбеддеры на localhost-портах (LM Studio/Ollama) и состояние индекса.
        """
        return tools.discover()

    @mcp.tool()
    def config_get() -> dict:
        """Текущие настройки (эффективный конфиг): эмбеддер, search.backend, bin_dir, книги."""
        return tools.config_get()

    @mcp.tool()
    def config_set(values: dict[str, Any]) -> dict:
        """Изменить настройки и сохранить в v8help.toml (атомарно).

        Ключи: search.backend (fts|hybrid|vectors), search.limit,
        search.max_chunks_per_page, build.cleanup, build.chunk_size,
        build.chunk_overlap, embedder.index/query.{model,base_url,api_key,dims,
        batch_size,embed_chars,threads}, bin_dir, lang, books.

        Args:
            values: Плоские ключи -> значения.
        """
        return tools.config_set(values)

    return mcp


def _load_config_at(path: Path) -> tuple[Config, str] | None:
    """(Config, путь), если файл есть и читается; иначе None (ошибка — в stderr)."""
    if not path.exists():
        return None
    try:
        return Config.load(path), str(path)
    except Exception as exc:
        print(f"[v8help] ошибка чтения {path}: {exc}", file=sys.stderr)
        return None


def load_config(config_arg: str | None) -> tuple[Config, str | None]:
    """Загрузка конфига. Порядок: --config > V8HELP_CONFIG > ./v8help.toml
    > PROJECT_ROOT/v8help.toml > defaults. Возвращает (config, config_path)."""
    path = config_arg or os.environ.get("V8HELP_CONFIG")
    if path:
        p = Path(path)
        loaded = _load_config_at(p)
        if loaded is None:
            if not p.exists():
                print(f"[v8help] config не найден: {p} — использую defaults", file=sys.stderr)
            return Config(), None
        return loaded
    for cand in (Path.cwd() / "v8help.toml", PROJECT_ROOT / "v8help.toml"):
        if cand.exists():
            return _load_config_at(cand) or (Config(), None)
    return Config(), None


def serve(
    config: Config,
    config_path: str | None = None,
    *,
    http: bool = False,
    host: str | None = None,
    port: int | None = None,
) -> int:
    """Запускает MCP-сервер: stdio (по умолчанию) или streamable-http."""
    base = Path(config_path).resolve().parent if config_path else PROJECT_ROOT
    config = _resolve_paths(config, base)
    mcp = build_server(config, config_path)
    if http:
        mcp.run(transport="http", host=host, port=port, show_banner=False)
    else:
        mcp.run(transport="stdio", show_banner=False)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    config_arg: str | None = None
    http = False
    host: str | None = None
    port: int | None = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--config" and i + 1 < len(argv):
            config_arg = argv[i + 1]
            i += 2
            continue
        if a == "--http":
            http = True
        elif a == "--host" and i + 1 < len(argv):
            host = argv[i + 1]
            i += 2
            continue
        elif a == "--port" and i + 1 < len(argv):
            port = int(argv[i + 1])
            i += 2
            continue
        i += 1
    config, config_path = load_config(config_arg)
    return serve(config, config_path, http=http, host=host, port=port)


if __name__ == "__main__":
    sys.exit(main())

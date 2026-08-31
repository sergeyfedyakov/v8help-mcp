"""CLI-интерфейс."""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict

from v8help import __version__
from v8help.config import Config
from v8help.db import Database
from v8help.indexer import run_build
from v8help.search.fts import FtsBackend


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="v8help",
        description="Инструмент для индексации и поиска по справке 1С (.hbk)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", metavar="PATH", help="путь к TOML-конфигу")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("build", help="распаковать .hbk, собрать корпус и индекс")
    p.add_argument("--sources", nargs="*", help="источники для сборки (по умолчанию из конфига)")
    p.add_argument("--lang", help="язык: ru/en (по умолчанию из конфига)")
    p.add_argument("--cleanup", action="store_true", help="удалить corpus после индексации")
    p.add_argument("--force", action="store_true", help="пересобрать даже если индекс актуален")
    p.add_argument("--chunk-size", type=int, help="целевой размер чанка в символах")
    p.add_argument("--chunk-overlap", type=int, help="перекрытие соседних чанков в символах")

    p = sub.add_parser("search", help="поиск по справке")
    p.add_argument("query", help="поисковый запрос")
    p.add_argument("--section", help="фильтр по разделу")
    p.add_argument("--kind", help="фильтр по kind")
    p.add_argument("--limit", type=int, default=None, help="максимум результатов")

    p = sub.add_parser("get-page", help="полный текст страницы")
    p.add_argument("id", help="идентификатор страницы (filename или числовой id)")
    p.add_argument("--chunk", type=int, default=None, help="номер чанка длинной статьи (0-based)")

    p = sub.add_parser("hierarchy", help="дерево TOC")
    p.add_argument("--section", help="фильтр по разделу")

    p = sub.add_parser("related", help="связанные страницы")
    p.add_argument("id", help="идентификатор страницы")

    p = sub.add_parser("serve", help="запустить MCP-сервер (stdio или --http)")
    p.add_argument("--http", action="store_true", help="HTTP-транспорт (streamable-http) вместо stdio")
    p.add_argument("--host", default=None, help="адрес для HTTP (по умолчанию 127.0.0.1)")
    p.add_argument("--port", type=int, default=None, help="порт для HTTP (по умолчанию 8000)")

    return parser


def _load_config(config_arg: str | None) -> Config:
    """Загрузка конфига с учётом V8HELP_CONFIG/env — та же логика, что в server.load_config."""
    from v8help import server

    config, _ = server.load_config(config_arg)
    return config


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = _load_config(args.config)
    handler = HANDLERS.get(args.command)
    if handler is None:
        parser.error(f"неизвестная команда: {args.command}")
    return handler(args, config)


def _lookup(conn, identifier: str):
    if identifier.isdigit():
        return conn.execute(
            "SELECT * FROM pages WHERE id=?", (int(identifier),)
        ).fetchone()
    return conn.execute(
        "SELECT * FROM pages WHERE filename=?", (identifier,)
    ).fetchone()


def _require_db(config: Config) -> Database | None:
    db = Database(config.db_path)
    if not db.exists():
        print(
            f"Индекс не найден: {config.db_path}. Сначала выполните 'v8help build'.",
            file=sys.stderr,
        )
        return None
    return db


def _build(args, config: Config) -> int:
    if args.sources:
        idset = set(args.sources)
        if config.sources:
            config.sources = [s for s in config.sources if s.id in idset]
        else:
            config.books = [b for b in config.books if b in idset]
    if args.lang:
        config.lang = args.lang
    if args.cleanup:
        config.build.cleanup = True
    if args.chunk_size:
        config.build.chunk_size = args.chunk_size
    if args.chunk_overlap:
        config.build.chunk_overlap = args.chunk_overlap
    result = run_build(config, force=args.force, on_progress=_print_progress)
    if result.skipped:
        print("Индекс актуален — пропуск.", file=sys.stderr)
        return 0
    print(f"Сборка завершена: {result.pages} страниц, {result.links} ссылок -> {result.db_path}")
    return 0


def _print_progress(stage: str, message: str) -> None:
    print(f"[{stage}] {message}", file=sys.stderr, flush=True)


def _search(args, config: Config) -> int:
    db = _require_db(config)
    if db is None:
        return 1
    limit = args.limit if args.limit is not None else config.search.limit
    backend = FtsBackend(config.db_path)
    results = backend.search(args.query, limit=limit, section=args.section, kind=args.kind)
    if not results:
        print("Ничего не найдено.")
        return 0
    for r in results:
        meta = f" {r.section}/{r.kind}" if (r.section or r.kind) else ""
        print(f"[{r.id}]{meta} {r.title}")
        print(f"    {r.snippet}")
    return 0


def _get_page(args, config: Config) -> int:
    db = _require_db(config)
    if db is None:
        return 1
    conn = db.connect()
    try:
        row = _lookup(conn, args.id)
        if row is None:
            print(f"Страница не найдена: {args.id}", file=sys.stderr)
            return 1
        if args.chunk is not None:
            chunk = conn.execute(
                "SELECT title, body FROM chunks WHERE page_id=? AND chunk_index=?",
                (row["id"], args.chunk),
            ).fetchone()
            if chunk is None:
                print(f"Чанк {args.chunk} не найден (статья не разбита или номер вне диапазона).",
                      file=sys.stderr)
                return 1
            print(f"# {row['title']} [чанк {args.chunk}]")
            print()
            print(chunk["body"])
            return 0
        print(f"# {row['title']}")
        print(f"id={row['id']} section={row['section']} kind={row['kind']} source={row['hbk_source']}")
        print()
        print(row["body"])
        return 0
    finally:
        conn.close()


def _hierarchy(args, config: Config) -> int:
    db = _require_db(config)
    if db is None:
        return 1
    conn = db.connect()
    try:
        if args.section:
            rows = conn.execute(
                "SELECT filename, title, kind FROM pages WHERE section=? ORDER BY filename",
                (args.section,),
            ).fetchall()
            groups: dict[str, list] = defaultdict(list)
            for r in rows:
                stem = r["filename"][:-3] if r["filename"].endswith(".md") else r["filename"]
                groups[stem.split(".", 1)[0]].append(r)
            for top in sorted(groups):
                print(f"## {top} ({len(groups[top])})")
        else:
            rows = conn.execute(
                "SELECT section, COUNT(*) AS n FROM pages GROUP BY section ORDER BY section"
            ).fetchall()
            for r in rows:
                print(f"{r['section']}: {r['n']}")
        return 0
    finally:
        conn.close()


def _related(args, config: Config) -> int:
    db = _require_db(config)
    if db is None:
        return 1
    conn = db.connect()
    try:
        row = _lookup(conn, args.id)
        if row is None:
            print(f"Страница не найдена: {args.id}", file=sys.stderr)
            return 1
        fname = row["filename"]
        out = conn.execute(
            "SELECT dst FROM links WHERE src=? ORDER BY dst", (fname,)
        ).fetchall()
        inc = conn.execute(
            "SELECT src FROM links WHERE dst=? ORDER BY src", (fname,)
        ).fetchall()
        print(f"# {row['title']} ({fname})")
        print(f"\nИсходящие ссылки ({len(out)}):")
        for r in out:
            print(f"  {r['dst']}")
        print(f"\nВходящие ссылки ({len(inc)}):")
        for r in inc:
            print(f"  {r['src']}")
        return 0
    finally:
        conn.close()


def _serve(args, config: Config) -> int:
    from v8help import server

    cfg, config_path = server.load_config(args.config)
    return server.serve(
        cfg,
        config_path=config_path,
        http=args.http,
        host=args.host,
        port=args.port,
    )


HANDLERS = {
    "build": _build,
    "search": _search,
    "get-page": _get_page,
    "hierarchy": _hierarchy,
    "related": _related,
    "serve": _serve,
}

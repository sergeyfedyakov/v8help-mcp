"""Инкрементальное добавление md-статей в существующую БД v8help.

Не пересобирает весь индекс: вставляет только НОВЫЕ страницы выбранного набора
(по умолчанию ``corpus_dir/<glob>``), строит чанки/ссылки и доэмбеддивает только
новые чанки (через ``embed_queue``). Существующие страницы не трогаются.

    python scripts/add_docs_incremental.py --glob "edtcli__*.md" \
        [--dir <каталог>] [--config v8help.toml] [--no-embed]

Типичный сценарий — справка по командной строке 1C:EDT (префикс ``edtcli__``),
сгенерированная в корпус: ``python -m v8help.edtcli --config v8help.toml``.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from v8help import lex, metadata
from v8help.config import Config
from v8help.db import Database
from v8help.search.chunker import chunk_text


def _resolve(cfg: Config, base: Path) -> Config:
    """Анкерует относительные пути конфига к base (как server._resolve_paths)."""
    for attr in ("corpus_dir", "db_path"):
        p = getattr(cfg, attr)
        if not p.is_absolute():
            setattr(cfg, attr, base / p)
    if str(cfg.edt_cli) not in ("", ".") and not cfg.edt_cli.is_absolute():
        cfg.edt_cli = base / cfg.edt_cli
    return cfg


def _read_files(cfg: Config, directory: str | None, glob: str) -> list[tuple[str, str]]:
    root = Path(directory) if directory else cfg.corpus_dir
    out: list[tuple[str, str]] = []
    for path in sorted(root.glob(glob)):
        name = path.name[:-3] if path.name.endswith(".md") else path.name
        out.append((name, path.read_text(encoding="utf-8", errors="replace")))
    return out


def _new_files(conn, files: list[tuple[str, str]]):
    existing = {r["filename"] for r in conn.execute("SELECT filename FROM pages")}
    return existing, [(n, t) for n, t in files if n not in existing]


def _insert_one(conn, db: Database, cfg: Config, name: str, text: str, known: set[str]):
    title = metadata.extract_title(text, name)
    page_id = db.insert_page(
        conn, name, title, metadata.detect_section(name),
        metadata.detect_kind(name), metadata.detect_source(name), "", text,
        lex.expand(title), lex.expand(text),
    )
    dsts = [d for d in metadata.parse_links(text) if d in known and d != name]
    db.insert_links(conn, name, dsts)
    parts = chunk_text(text, chunk_size=cfg.build.chunk_size, overlap=cfg.build.chunk_overlap)
    ids = [
        db.insert_chunk(
            conn, page_id, i, title, body,
            "page" if len(parts) == 1 else "chunk",
            metadata.extract_description(text),
        )
        for i, body in enumerate(parts)
    ]
    if len(ids) > 1:
        db.link_chunks(conn, ids)
    db.enqueue_chunks(conn, [(cid, title, body) for cid, body in zip(ids, parts)])
    return len(dsts), len(ids)


def _insert_files(conn, db: Database, cfg: Config, files: list[tuple[str, str]]):
    existing, new_files = _new_files(conn, files)
    if not new_files:
        return 0, 0, 0
    known = existing | {n for n, _ in new_files}
    links = chunks = 0
    for name, text in new_files:
        add_links, add_chunks = _insert_one(conn, db, cfg, name, text, known)
        links += add_links
        chunks += add_chunks
    return len(new_files), links, chunks


def _bump_meta(conn) -> None:
    total = conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('pages',?)", (str(total),))
    conn.execute(
        "INSERT OR REPLACE INTO meta(key,value) VALUES('indexed_at',?)",
        (str(int(time.time())),),
    )


def _store(config: Config, files: list[tuple[str, str]]):
    db = Database(config.db_path)
    if not db.exists():
        raise FileNotFoundError(str(config.db_path))
    conn = db.connect()
    try:
        conn.execute("BEGIN")
        counts = _insert_files(conn, db, config, files)
        if counts[0]:
            _bump_meta(conn)
        conn.execute("COMMIT")
        return counts
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _embed(config: Config, no_embed: bool) -> None:
    e = config.embedder_index
    if no_embed or not (e.base_url and e.model):
        print("Эмбеддинги пропущены (--no-embed или эмбеддер не задан).")
        return
    from v8help.indexer import _index_vectors

    vectors = _index_vectors(
        config, config.db_path, lambda s, m: print(f"[{s}] {m}", file=sys.stderr)
    )
    print(f"Векторов: {vectors}")


def _parse(argv: list[str]):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--glob", default="edtcli__*.md", help="маска файлов в корпусе")
    parser.add_argument("--dir", help="каталог с md (по умолчанию corpus_dir)")
    parser.add_argument("--config", help="путь к v8help.toml")
    parser.add_argument("--no-embed", action="store_true", help="не считать эмбеддинги")
    return parser.parse_args(argv)


def _load_config(path: Path) -> Config | None:
    if not path.exists():
        print(f"Конфиг не найден: {path}", file=sys.stderr)
        return None
    return _resolve(Config.load(path), path.resolve().parent)


def main(argv: list[str]) -> int:
    args = _parse(argv)
    config = _load_config(Path(args.config) if args.config else Path("v8help.toml"))
    if config is None:
        return 2
    files = _read_files(config, args.dir, args.glob)
    if not files:
        print(f"Нет файлов по маске {args.glob} в {args.dir or config.corpus_dir}", file=sys.stderr)
        return 2
    try:
        pages, links, chunks = _store(config, files)
    except FileNotFoundError as exc:
        print(f"БД не найдена: {exc} — сначала полная сборка.", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 — понятное сообщение вместо трейсбека
        print(f"ОШИБКА при вставке: {exc}", file=sys.stderr)
        return 1
    if not pages:
        print("Новых страниц нет — все файлы уже в индексе.")
        return 0
    print(f"Добавлено страниц: {pages}, ссылок: {links}, чанков: {chunks}")
    _embed(config, args.no_embed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

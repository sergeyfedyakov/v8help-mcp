"""Инкрементальное добавление книги справки (.hbk) в существующую БД.

Не пересобирает весь индекс: конвертирует ТОЛЬКО указанную книгу, вставляет её
страницы/чанки/ссылки в уже собранную БД и доэмбеддивает только новые чанки
(через embed_queue). Существующие страницы не трогаются, старые книги не
затрагиваются.

    python scripts/add_book_incremental.py <book_id> [--config v8help.toml] [--no-embed]

Перед запуском книга должна быть добавлена в список ``books`` конфига (иначе её
префикс/схема не определится). book_id — например ``dcsui_ru``.

В CLI пакета не добавляется: это разовый инструмент для обогащения готовой БД.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

from v8help import lex, metadata
from v8help.config import Config, SourceSpec, _book_lang, _book_meta
from v8help.converter import HbkConverter
from v8help.db import Database
from v8help.search.chunker import chunk_text


def _resolve(cfg: Config, base: Path) -> Config:
    """Анкерует относительные пути конфига к base (как server._resolve_paths)."""

    def _abs(p: Path) -> Path:
        return p if p.is_absolute() else base / p

    cfg.corpus_dir = _abs(cfg.corpus_dir)
    cfg.db_path = _abs(cfg.db_path)
    if str(cfg.bin_dir) not in ("", "."):
        cfg.bin_dir = _abs(cfg.bin_dir)
    return cfg


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("book", help="идентификатор книги, напр. dcsui_ru")
    parser.add_argument("--config", help="путь к v8help.toml")
    parser.add_argument("--no-embed", action="store_true", help="не считать эмбеддинги")
    args = parser.parse_args(argv)

    cfg_path = Path(args.config) if args.config else Path("v8help.toml")
    base = cfg_path.resolve().parent
    if cfg_path.exists():
        config = Config.load(cfg_path)
    else:
        print(f"Конфиг не найден: {cfg_path}", file=sys.stderr)
        return 2
    config = _resolve(config, base)

    bin_dir = config.resolve_bin_dir()
    if str(bin_dir) in ("", "."):
        print("bin_dir не задан и не найден автоматически.", file=sys.stderr)
        return 2
    prefix, scheme = _book_meta(args.book)
    src = SourceSpec(
        id=args.book,
        hbk=bin_dir / f"{args.book}.hbk",
        prefix=prefix,
        scheme=scheme,
        lang=_book_lang(args.book, config.lang),
    )
    if not src.hbk.exists():
        print(f"Книга не найдена: {src.hbk}", file=sys.stderr)
        return 2

    db = Database(config.db_path)
    if not db.exists():
        print(f"БД не найдена: {config.db_path} — сначала полная сборка.", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="v8help-add-") as td:
        out = Path(td) / "corpus"
        conv = HbkConverter([src], out, on_progress=lambda s, m: print(f"[{s}] {m}", file=sys.stderr))
        res = conv.run()
        files = [
            (f.name[:-3] if f.name.endswith(".md") else f.name, f.read_text(encoding="utf-8", errors="replace"))
            for f in res.files
        ]
    print(f"Сконвертировано: {len(files)} страниц")

    conn = db.connect()
    existing = {r["filename"] for r in conn.execute("SELECT filename FROM pages")}
    new_files = [(name, text) for name, text in files if name not in existing]
    if not new_files:
        print("Новых страниц нет — книга уже в индексе.")
        conn.close()
        return 0
    known = existing | {name for name, _ in new_files}

    chunk_size = config.build.chunk_size
    chunk_overlap = config.build.chunk_overlap
    pages = links = chunks = 0
    try:
        conn.execute("BEGIN")
        for name, text in new_files:
            title = metadata.extract_title(text, name)
            section = metadata.detect_section(name)
            kind = metadata.detect_kind(name)
            source = metadata.detect_source(name)
            description = metadata.extract_description(text)
            title_search = lex.expand(title)
            body_search = lex.expand(text)
            page_id = db.insert_page(
                conn, name, title, section, kind, source, "", text,
                title_search, body_search,
            )
            dsts = [d for d in metadata.parse_links(text) if d in known and d != name]
            db.insert_links(conn, name, dsts)
            links += len(dsts)

            parts = chunk_text(text, chunk_size=chunk_size, overlap=chunk_overlap)
            ids: list[int] = []
            for idx, body in enumerate(parts):
                s = "page" if len(parts) == 1 else "chunk"
                cid = db.insert_chunk(conn, page_id, idx, title, body, s, description)
                ids.append(cid)
            if len(ids) > 1:
                db.link_chunks(conn, ids)
            db.enqueue_chunks(conn, [(cid, title, body) for cid, body in zip(ids, parts)])
            pages += 1
            chunks += len(ids)
        total_pages = conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
        conn.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES('pages',?)",
            (str(total_pages),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES('indexed_at',?)",
            (str(int(time.time())),),
        )
        conn.execute("COMMIT")
    except Exception as exc:
        conn.rollback()
        print(f"ОШИБКА при вставке: {exc}", file=sys.stderr)
        conn.close()
        return 1
    finally:
        conn.close()
    print(f"Добавлено страниц: {pages}, ссылок: {links}, чанков: {chunks}")

    vectors = 0
    e = config.embedder_index
    if not args.no_embed and e.base_url and e.model:
        from v8help.indexer import _index_vectors

        vectors = _index_vectors(config, config.db_path, lambda s, m: print(f"[{s}] {m}", file=sys.stderr))
        print(f"Векторов: {vectors}")
    else:
        print("Эмбеддинги пропущены (--no-embed или эмбеддер не задан).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

"""Индексация markdown-корпуса в SQLite (pages, pages_fts, links).

Сборка пишет во временную БД и атомарно подменяет целевую (``os.replace``),
чтобы поиск во время сборки читал старый индекс без блокировок и не видел
полуготовое состояние.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from v8help import lex, metadata
from v8help.config import Config
from v8help.converter import consolidate
from v8help.db import Database

ProgressFn = Callable[[str, str], None]


@dataclass
class BuildResult:
    skipped: bool = False
    pages: int = 0
    links: int = 0
    sources: int = 0
    duration_sec: float = 0.0
    db_path: str = ""


def _is_up_to_date(config: Config) -> bool:
    db = Path(config.db_path)
    if not db.exists():
        return False
    try:
        db_mtime = db.stat().st_mtime
    except OSError:
        return False
    for src in config.resolve_sources():
        if src.hbk.exists() and src.hbk.stat().st_mtime > db_mtime:
            return False
    return True


def _atomic_replace(src: Path, dst: Path) -> None:
    for attempt in range(10):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.05)


def _index_corpus(config: Config, db_path: Path, files: list[Path]) -> tuple[int, int]:
    db = Database(db_path)
    conn = db.connect()
    total = 0
    links_total = 0
    try:
        db.reset(conn)
        filenames = {_stem(p.name) for p in files}
        conn.execute("BEGIN")
        for p in files:
            text = p.read_text(encoding="utf-8", errors="replace")
            name = _stem(p.name)
            title = metadata.extract_title(text, name)
            section = metadata.detect_section(name)
            kind = metadata.detect_kind(name)
            source = metadata.detect_source(name)
            search_text = lex.expand(title + "\n" + text)
            db.insert_page(conn, name, title, section, kind, source, "", text, search_text)
            dsts = [d for d in metadata.parse_links(text) if d in filenames and d != name]
            db.insert_links(conn, name, dsts)
            links_total += len(dsts)
            total += 1
        conn.execute(
            "INSERT INTO meta(key,value) VALUES('indexed_at',?)",
            (str(int(time.time())),),
        )
        conn.execute(
            "INSERT INTO meta(key,value) VALUES('pages',?)",
            (str(total),),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return total, links_total


def run_build(
    config: Config,
    force: bool = False,
    on_progress: ProgressFn | None = None,
) -> BuildResult:
    started = time.time()
    emit = on_progress or (lambda stage, msg: None)
    sources = config.resolve_sources()
    result = BuildResult(sources=len(sources), db_path=str(config.db_path))

    if not force and _is_up_to_date(config):
        result.skipped = True
        result.duration_sec = round(time.time() - started, 2)
        emit("skip", "Индекс актуален, пропуск")
        return result

    corpus = config.corpus_dir
    emit("consolidate", f"Консолидация корпуса ({len(sources)} источников)")
    consolidate(config, on_progress)

    files = sorted(corpus.glob("*.md"))
    emit("index", f"Индексация {len(files)} страниц")

    tmp_db = config.db_path.with_name(config.db_path.name + f".tmp-{os.getpid()}")
    if tmp_db.exists():
        tmp_db.unlink()
    total, links_total = _index_corpus(config, tmp_db, files)
    _atomic_replace(tmp_db, config.db_path)

    if config.build.cleanup:
        shutil.rmtree(corpus, ignore_errors=True)

    result.pages = total
    result.links = links_total
    result.duration_sec = round(time.time() - started, 2)
    emit("done", f"Готово: {total} страниц, {links_total} ссылок")
    return result


def build_index(config: Config) -> int:
    """Совместимость: синхронная сборка, возвращает код выхода (0 при успехе)."""
    run_build(config)
    return 0


def _stem(name: str) -> str:
    return name[:-3] if name.endswith(".md") else name

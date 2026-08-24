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

import numpy as np

from v8help import __version__, lex, metadata
from v8help.config import Config, discover_platforms
from v8help.converter import consolidate
from v8help.db import Database
from v8help.search.embedder import Embedder

ProgressFn = Callable[[str, str], None]


@dataclass
class BuildResult:
    skipped: bool = False
    pages: int = 0
    links: int = 0
    sources: int = 0
    duration_sec: float = 0.0
    db_path: str = ""
    bin_dir: str = ""
    vectors: int = 0
    embed_model: str = ""
    embed_dims: int = 0
    embed_chars: int = 0


def _read_meta(db_path: Path) -> dict[str, str]:
    db = Database(db_path)
    conn = db.connect()
    try:
        return dict(conn.execute("SELECT key, value FROM meta").fetchall())
    finally:
        conn.close()


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
    # Если эмбеддер задан/поменялся — векторы могли устареть.
    e = config.embedder_index
    try:
        meta = _read_meta(db)
    except Exception:
        return False
    if e.base_url and e.model:
        if (
            meta.get("embed_model") != e.model
            or meta.get("embed_dims") != str(e.dims)
            or meta.get("embed_chars") != str(e.embed_chars)
        ):
            return False
    elif meta.get("embed_model"):
        # Векторы были, а теперь эмбеддер отключён — пересобрать без них.
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


def _index_corpus(
    config: Config,
    db_path: Path,
    files: list[Path],
    extra_meta: dict[str, str] | None = None,
) -> tuple[int, int]:
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
            title_search = lex.expand(title)
            body_search = lex.expand(text)
            db.insert_page(
                conn, name, title, section, kind, source, "", text,
                title_search, body_search,
            )
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
        for key, value in (extra_meta or {}).items():
            conn.execute(
                "INSERT INTO meta(key,value) VALUES(?,?)",
                (key, str(value)),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return total, links_total


def _bin_dir_version(bin_dir: Path) -> str:
    if str(bin_dir) in ("", "."):
        return ""
    for p in discover_platforms():
        if Path(p["bin_dir"]) == bin_dir:
            return p["version"]
    name = bin_dir.parent.name if bin_dir.name.lower() == "bin" else bin_dir.name
    from v8help.config import _parse_version

    v = _parse_version(name)
    return ".".join(map(str, v)) if v else str(bin_dir)


def _index_vectors(config: Config, db_path: Path, emit: ProgressFn) -> int:
    """Считает эмбеддинги страниц и пишет нормализованные векторы в tmp-БД."""
    e = config.embedder_index
    if not (e.base_url and e.model):
        return 0
    embedder = Embedder(e)
    db = Database(db_path)
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT id, title, body FROM pages ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return 0

    ids = [r["id"] for r in rows]
    texts = [f"{r['title']}\n{r['body'][:e.embed_chars]}" for r in rows]

    emit("embed", f"Эмбеддинги {len(texts)} страниц ({e.model})")
    vecs = embedder.embed_batches(
        texts,
        on_batch=lambda done, total: emit("embed", f"{done}/{total}"),
    )

    conn = db.connect()
    try:
        conn.execute("BEGIN")
        for pid, v in zip(ids, vecs):
            arr = np.asarray(v, dtype=np.float32)
            n = float(np.linalg.norm(arr))
            if n:
                arr = arr / n
            db.insert_vector(conn, pid, arr.tobytes())
        conn.execute("COMMIT")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return len(vecs)


def run_build(
    config: Config,
    force: bool = False,
    on_progress: ProgressFn | None = None,
) -> BuildResult:
    started = time.time()
    emit = on_progress or (lambda stage, msg: None)
    sources = config.resolve_sources()
    bd = config.resolve_bin_dir()
    result = BuildResult(
        sources=len(sources),
        db_path=str(config.db_path),
        bin_dir=str(bd) if str(bd) not in ("", ".") else "",
    )

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

    e = config.embedder_index
    embed_configured = bool(e.base_url and e.model)
    extra_meta: dict[str, str] = {
        "v8help_version": __version__,
        "platform_version": _bin_dir_version(bd),
    }
    if embed_configured:
        extra_meta["embed_model"] = e.model
        extra_meta["embed_dims"] = str(e.dims)
        extra_meta["embed_chars"] = str(e.embed_chars)

    tmp_db = config.db_path.with_name(config.db_path.name + f".tmp-{os.getpid()}")
    if tmp_db.exists():
        tmp_db.unlink()
    total, links_total = _index_corpus(config, tmp_db, files, extra_meta)

    vectors = 0
    if embed_configured:
        vectors = _index_vectors(config, tmp_db, emit)

    _atomic_replace(tmp_db, config.db_path)

    if config.build.cleanup:
        shutil.rmtree(corpus, ignore_errors=True)

    result.pages = total
    result.links = links_total
    result.vectors = vectors
    result.embed_model = e.model if embed_configured else ""
    result.embed_dims = e.dims if embed_configured else 0
    result.embed_chars = e.embed_chars if embed_configured else 0
    result.duration_sec = round(time.time() - started, 2)
    done_msg = f"Готово: {total} страниц, {links_total} ссылок"
    if vectors:
        done_msg += f", {vectors} векторов"
    emit("done", done_msg)
    return result


def build_index(config: Config) -> int:
    """Совместимость: синхронная сборка, возвращает код выхода (0 при успехе)."""
    run_build(config)
    return 0


def _stem(name: str) -> str:
    return name[:-3] if name.endswith(".md") else name

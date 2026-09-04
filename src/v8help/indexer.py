"""Индексация markdown-корпуса в SQLite (pages, chunks, links, vectors).

Сборка пишет во временную БД и атомарно подменяет целевую (``os.replace``),
чтобы поиск во время сборки читал старый индекс без блокировок и не видел
полуготовое состояние.

Страницы делятся на чанки (см. ``search.chunker``): чанки — базовая единица
поиска (FTS и векторы). Для коротких статей (≤ chunk_size) чанк один = вся
статья.
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
from v8help.search.chunker import chunk_text
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
    chunks: int = 0
    vectors: int = 0
    embed_model: str = ""
    embed_dims: int = 0
    embed_chars: int = 0
    chunk_size: int = 1500
    chunk_overlap: int = 200
    threads: int = 0


def _read_meta(db_path: Path) -> dict[str, str]:
    db = Database(db_path)
    conn = db.connect()
    try:
        return dict(conn.execute("SELECT key, value FROM meta").fetchall())
    finally:
        conn.close()


def _embed_meta_matches(config: Config, meta: dict[str, str]) -> bool:
    """В мета согласованы модель/dims/embed_chars с ``embedder.index``.

    Без настроенного эмбеддера всегда True.
    """
    e = config.embedder_index
    if not (e.base_url and e.model):
        return True
    return (
        meta.get("embed_model") == e.model
        and meta.get("embed_dims") == str(e.dims)
        and meta.get("embed_chars") == str(e.embed_chars)
    )


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
    try:
        meta = _read_meta(db)
    except Exception:
        return False
    # Если эмбеддер задан/поменялся — векторы могли устареть; если отключён,
    # а векторы были — пересобрать без них.
    if not _embed_meta_matches(config, meta):
        return False
    if meta.get("embed_model") and not (
        config.embedder_index.base_url and config.embedder_index.model
    ):
        return False
    if (meta.get("chunk_size") != str(config.build.chunk_size)
            or meta.get("chunk_overlap") != str(config.build.chunk_overlap)):
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


def _index_page_row(db, conn, path, filenames):
    """Вставляет строку pages и исходящие ссылки. Возвращает (title, text, page_id, links)."""
    name = _stem(path.name)
    text = path.read_text(encoding="utf-8", errors="replace")
    title = metadata.extract_title(text, name)
    page_id = db.insert_page(
        conn, name, title, metadata.detect_section(name),
        metadata.detect_kind(name), metadata.detect_source(name), "", text,
        lex.expand(title), lex.expand(text),
    )
    dsts = [d for d in metadata.parse_links(text) if d in filenames and d != name]
    db.insert_links(conn, name, dsts)
    return title, text, page_id, len(dsts)


def _index_one_file(db, conn, path, filenames, chunk_size, chunk_overlap, enqueue_embeds):
    """md → page+chunks+ссылки; возвращает (links, chunks)."""
    title, text, page_id, links = _index_page_row(db, conn, path, filenames)
    desc = metadata.extract_description(text)
    parts = chunk_text(text, chunk_size=chunk_size, overlap=chunk_overlap)
    src = "page" if len(parts) == 1 else "chunk"
    ids = [db.insert_chunk(conn, page_id, i, title, b, src, desc)
           for i, b in enumerate(parts)]
    if len(ids) > 1:
        db.link_chunks(conn, ids)
    if enqueue_embeds:
        db.enqueue_chunks(conn, [(cid, title, b) for cid, b in zip(ids, parts)])
    return links, len(ids)


def _write_corpus_meta(conn, pages: int, extra_meta: dict[str, str] | None) -> None:
    conn.execute(
        "INSERT INTO meta(key,value) VALUES('indexed_at',?)",
        (str(int(time.time())),),
    )
    conn.execute(
        "INSERT INTO meta(key,value) VALUES('pages',?)",
        (str(pages),),
    )
    for key, value in (extra_meta or {}).items():
        conn.execute(
            "INSERT INTO meta(key,value) VALUES(?,?)",
            (key, str(value)),
        )


def _index_corpus(
    config: Config,
    db_path: Path,
    files: list[Path],
    extra_meta: dict[str, str] | None = None,
    enqueue_embeds: bool = False,
) -> tuple[int, int, int]:
    db = Database(db_path)
    conn = db.connect()
    total = 0
    links_total = 0
    chunks_total = 0
    chunk_size = config.build.chunk_size
    chunk_overlap = config.build.chunk_overlap
    try:
        db.reset(conn)
        filenames = {_stem(p.name) for p in files}
        conn.execute("BEGIN")
        for p in files:
            links, chunks = _index_one_file(
                db, conn, p, filenames, chunk_size, chunk_overlap, enqueue_embeds,
            )
            links_total += links
            chunks_total += chunks
            total += 1
        _write_corpus_meta(conn, total, extra_meta)
        conn.execute("COMMIT")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return total, links_total, chunks_total


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


def _tmp_path(config: Config) -> Path:
    """Фиксированное имя tmp-БД: переживает рестарт процесса (resume)."""
    return config.db_path.with_name(config.db_path.name + ".tmp")


def _resume_meta_matches(config: Config, meta: dict[str, str]) -> bool:
    if meta.get("chunk_size") != str(config.build.chunk_size):
        return False
    if meta.get("chunk_overlap") != str(config.build.chunk_overlap):
        return False
    e = config.embedder_index
    if not (e.base_url and e.model):
        # Эмбеддер не настроен — в tmp нечего эмбеддить, нужен полный пересбор.
        return False
    return _embed_meta_matches(config, meta)


def _embed_pending(tmp_db: Path) -> bool:
    """Осталось что-то эмбеддить: план ``embed_queue`` непуст или векторы неполные."""
    db = Database(tmp_db)
    conn = db.connect()
    try:
        has_queue = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='embed_queue'"
        ).fetchone()
        if not has_queue:
            return False
        pending = conn.execute("SELECT COUNT(*) FROM embed_queue").fetchone()[0]
        chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        vectors = conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
    except Exception:
        return False
    finally:
        conn.close()
    return pending > 0 or vectors < chunks


def _resume_candidate(config: Config, tmp_db: Path) -> bool:
    """Можно ли возобновить сборку из частично собранной tmp-БД.

    Условия: tmp существует и валидна; мета (chunk_size/chunk_overlap/эмбеддер)
    совпадает с конфигом; исходники не новее tmp; остался план ``embed_queue``
    (или есть чанки без векторов) — т.е. эмбеддинг не завершён.
    """
    if not tmp_db.exists():
        return False
    try:
        meta = _read_meta(tmp_db)
    except Exception:
        return False
    if not _resume_meta_matches(config, meta):
        return False
    try:
        tmp_mtime = tmp_db.stat().st_mtime
    except OSError:
        return False
    for src in config.resolve_sources():
        if src.hbk.exists() and src.hbk.stat().st_mtime > tmp_mtime:
            return False
    return _embed_pending(tmp_db)


def _index_vectors(config: Config, db_path: Path, emit: ProgressFn) -> int:
    """Эмбеддинги чанков tmp-БД по плану ``embed_queue`` → нормализованные векторы.

    Текст для эмбеддинга — ``title + "\\n" + body`` без усечения (чанк уже
    ограничен ``chunk_size``). Работаем суперпачками с коммитом на пачку — при
    сбое возобновление с остатка плана (см. ``_embed_and_store``).
    """
    e = config.embedder_index
    if not (e.base_url and e.model):
        return 0
    embedder = Embedder(e)
    db = Database(db_path)
    conn = db.connect()
    try:
        total = conn.execute("SELECT COUNT(*) FROM embed_queue").fetchone()[0]
        if total == 0:
            return conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
        emit("embed", f"Эмбеддинги {total} чанков ({e.model})")
        _embed_loop(db, conn, embedder, e, total, emit)
        return conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
    finally:
        conn.close()


def _embed_loop(db, conn, embedder, e, total, emit) -> None:
    # Суперпачка: несколько батчей на поток, чтобы COMMIT был не слишком частым.
    bs = e.batch_size or 64
    super_batch = max(bs * max(1, e.threads or 1) * 2, bs)
    done = 0
    while True:
        rows = conn.execute(
            "SELECT chunk_id, title, body FROM embed_queue"
            " ORDER BY chunk_id LIMIT ?",
            (super_batch,),
        ).fetchall()
        if not rows:
            break
        _embed_and_store(db, conn, embedder, e, rows)
        done += len(rows)
        emit("embed", f"{done}/{total}")


def _embed_and_store(db, conn, embedder, e, rows) -> None:
    """Эмбеддит суперпачку: векторы и снятие чанков с плана — одной транзакцией.

    При прерывании (рестарт процесса, сбой эмбеддера) теряется не более одной
    суперпачки: закоммиченные векторы остаются, план показывает остаток.
    """
    ids = [r["chunk_id"] for r in rows]
    vecs = embedder.embed_batches(
        [f"{r['title']}\n{r['body']}" for r in rows],
        threads=max(1, e.threads or 1),
    )
    blob_rows = []
    for cid, v in zip(ids, vecs):
        arr = np.asarray(v, dtype=np.float32)
        n = float(np.linalg.norm(arr))
        if n:
            arr = arr / n
        blob_rows.append((cid, arr.tobytes()))
    conn.execute("BEGIN")
    try:
        db.insert_vectors(conn, blob_rows)
        db.dequeue_chunks(conn, ids)
        conn.execute("COMMIT")
    except Exception:
        conn.rollback()
        raise


def _build_extra_meta(config: Config, bd: Path) -> dict[str, str]:
    e = config.embedder_index
    extra: dict[str, str] = {
        "v8help_version": __version__,
        "platform_version": _bin_dir_version(bd),
        "chunk_size": str(config.build.chunk_size),
        "chunk_overlap": str(config.build.chunk_overlap),
    }
    if e.base_url and e.model:
        extra["embed_model"] = e.model
        extra["embed_dims"] = str(e.dims)
        extra["embed_chars"] = str(e.embed_chars)
    return extra


def _consolidate_and_index(
    config: Config,
    tmp_db: Path,
    sources: list,
    embed_configured: bool,
    extra_meta: dict[str, str],
    on_progress: ProgressFn | None,
    emit: ProgressFn,
) -> tuple[int, int, int]:
    """Полная сборка: конвертация корпуса в md + индексация страниц в tmp-БД."""
    corpus = config.corpus_dir
    emit("consolidate", f"Консолидация корпуса ({len(sources)} источников)")
    consolidate(config, on_progress)

    files = sorted(corpus.glob("*.md"))
    emit("index", f"Индексация {len(files)} страниц")

    for stale in config.db_path.parent.glob(config.db_path.name + ".tmp-*"):
        try:
            stale.unlink()
        except OSError:
            pass
    if tmp_db.exists():
        tmp_db.unlink()
    return _index_corpus(
        config, tmp_db, files, extra_meta, enqueue_embeds=embed_configured,
    )


def _tmp_counts(tmp_db: Path) -> tuple[int, int, int]:
    """Счётчики pages/links/chunks из готовой tmp-БД (resume-режим)."""
    db = Database(tmp_db)
    conn = db.connect()
    try:
        return (
            conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM links").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
        )
    finally:
        conn.close()


def _fill_result(
    result: BuildResult,
    config: Config,
    counts: tuple[int, int, int],
    vectors: int,
    embed_configured: bool,
    started: float,
) -> None:
    e = config.embedder_index
    result.pages, result.links, result.chunks = counts
    result.vectors = vectors
    result.embed_model = e.model if embed_configured else ""
    result.embed_dims = e.dims if embed_configured else 0
    result.embed_chars = e.embed_chars if embed_configured else 0
    result.chunk_size = config.build.chunk_size
    result.chunk_overlap = config.build.chunk_overlap
    result.threads = e.threads if embed_configured else 0
    result.duration_sec = round(time.time() - started, 2)


def _build_in_tmp(config, tmp_db, sources, bd, embed_configured, resumed,
                  on_progress, emit):
    """Готовит содержимое tmp-БД: корпус+чанки (или resume) и векторы.

    Возвращает (counts pages/links/chunks, vectors). Подмену целевой БД делает
    вызывающий (run_build).
    """
    if resumed:
        vectors = _index_vectors(config, tmp_db, emit) if embed_configured else 0
        # В resume-режиме счётчики читаем из готовой tmp-БД до подмены.
        return _tmp_counts(tmp_db), vectors
    counts = _consolidate_and_index(
        config, tmp_db, sources, embed_configured,
        _build_extra_meta(config, bd), on_progress, emit,
    )
    vectors = _index_vectors(config, tmp_db, emit) if embed_configured else 0
    return counts, vectors


def _finish_build(result, config, tmp_db, counts, vectors, embed_configured,
                  started, emit):
    _atomic_replace(tmp_db, config.db_path)
    if config.build.cleanup:
        shutil.rmtree(config.corpus_dir, ignore_errors=True)
    _fill_result(result, config, counts, vectors, embed_configured, started)
    done_msg = f"Готово: {counts[0]} страниц, {counts[1]} ссылок, {counts[2]} чанков"
    if vectors:
        done_msg += f", {vectors} векторов"
    emit("done", done_msg)


def _tmp_state(config, force, emit):
    """Путь tmp-БД, флаг настроенного эмбеддера, флаг возобновления сборки."""
    # Фиксированное имя tmp-БД (без pid) — ради resume — см. _tmp_path.
    tmp_db = _tmp_path(config)
    e = config.embedder_index
    embed_configured = bool(e.base_url and e.model)
    resumed = not force and _resume_candidate(config, tmp_db)
    if resumed:
        emit("embed", "Найдена незавершённая tmp-БД, возобновление эмбеддинга")
    return tmp_db, embed_configured, resumed


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
        sources=len(sources), db_path=str(config.db_path),
        bin_dir=str(bd) if str(bd) not in ("", ".") else "",
    )

    if not force and _is_up_to_date(config):
        result.skipped = True
        result.duration_sec = round(time.time() - started, 2)
        emit("skip", "Индекс актуален, пропуск")
        return result

    tmp_db, embed_configured, resumed = _tmp_state(config, force, emit)
    counts, vectors = _build_in_tmp(
        config, tmp_db, sources, bd, embed_configured, resumed, on_progress, emit,
    )
    _finish_build(result, config, tmp_db, counts, vectors, embed_configured,
                  started, emit)
    return result


def build_index(config: Config) -> int:
    """Совместимость: синхронная сборка, возвращает код выхода (0 при успехе)."""
    run_build(config)
    return 0


def _stem(name: str) -> str:
    return name[:-3] if name.endswith(".md") else name

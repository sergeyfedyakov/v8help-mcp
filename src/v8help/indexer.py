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
    if meta.get("chunk_size") != str(config.build.chunk_size):
        return False
    if meta.get("chunk_overlap") != str(config.build.chunk_overlap):
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
            text = p.read_text(encoding="utf-8", errors="replace")
            name = _stem(p.name)
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
            dsts = [d for d in metadata.parse_links(text) if d in filenames and d != name]
            db.insert_links(conn, name, dsts)
            links_total += len(dsts)

            parts = chunk_text(text, chunk_size=chunk_size, overlap=chunk_overlap)
            ids: list[int] = []
            for idx, body in enumerate(parts):
                src = "page" if len(parts) == 1 else "chunk"
                cid = db.insert_chunk(
                    conn, page_id, idx, title, body, src, description,
                )
                ids.append(cid)
            if len(ids) > 1:
                db.link_chunks(conn, ids)
            chunks_total += len(ids)
            if enqueue_embeds:
                db.enqueue_chunks(conn, [(cid, title, body) for cid, body in zip(ids, parts)])
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
    if meta.get("chunk_size") != str(config.build.chunk_size):
        return False
    if meta.get("chunk_overlap") != str(config.build.chunk_overlap):
        return False
    e = config.embedder_index
    if e.base_url and e.model:
        if (
            meta.get("embed_model") != e.model
            or meta.get("embed_dims") != str(e.dims)
            or meta.get("embed_chars") != str(e.embed_chars)
        ):
            return False
    else:
        # Эмбеддер не настроен — в tmp ничего эмбеддить, нужен полный пересбор.
        return False
    try:
        tmp_mtime = tmp_db.stat().st_mtime
    except OSError:
        return False
    for src in config.resolve_sources():
        if src.hbk.exists() and src.hbk.stat().st_mtime > tmp_mtime:
            return False
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
    # Осталось что-то эмбеддить: план непуст или векторы неполные.
    return pending > 0 or vectors < chunks


def _index_vectors(config: Config, db_path: Path, emit: ProgressFn) -> int:
    """Считает эмбеддинги чанков и пишет нормализованные векторы в tmp-БД.

    Текст для эмбеддинга — ``title + "\\n" + body`` (чанк целиком, без усечения
    ``embed_chars``: чанк уже ограничен ``chunk_size``). Батчи эмбеддятся в
    ``threads`` потоках.

    Работаем по плану ``embed_queue``: берём суперпачку, эмбеддим, пишем векторы
    и снимаем чанки с плана в ОДНОЙ транзакции (COMMIT). Таким образом при
    прерывании (рестарт сервера/процесса, сбой эмбеддера) теряется не более
    одной суперпачки: уже закоммиченные векторы остаются, план в БД показывает
    точный остаток для возобновления.
    """
    e = config.embedder_index
    if not (e.base_url and e.model):
        return 0
    embedder = Embedder(e)
    db = Database(db_path)
    conn = db.connect()
    try:
        pending = conn.execute("SELECT COUNT(*) FROM embed_queue").fetchone()[0]
        if pending == 0:
            return conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
        total = pending
        bs = e.batch_size or 64
        threads = max(1, e.threads or 1)
        # Суперпачка: несколько батчей на поток, чтобы COMMIT был не слишком частым.
        super_batch = max(bs * threads * 2, bs)
        emit("embed", f"Эмбеддинги {total} чанков ({e.model})")
        done = 0
        while True:
            rows = conn.execute(
                "SELECT chunk_id, title, body FROM embed_queue"
                " ORDER BY chunk_id LIMIT ?",
                (super_batch,),
            ).fetchall()
            if not rows:
                break
            ids = [r["chunk_id"] for r in rows]
            texts = [f"{r['title']}\n{r['body']}" for r in rows]
            vecs = embedder.embed_batches(
                texts,
                threads=threads,
                on_batch=lambda d, t: None,
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
            done += len(ids)
            emit("embed", f"{done}/{total}")
        return conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
    finally:
        conn.close()


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

    # Фиксированное имя tmp-БД (без pid): позволяет возобновить эмбеддинг
    # после рестарта сервера — см. _resume_candidate.
    tmp_db = _tmp_path(config)

    e = config.embedder_index
    embed_configured = bool(e.base_url and e.model)
    extra_meta: dict[str, str] = {
        "v8help_version": __version__,
        "platform_version": _bin_dir_version(bd),
        "chunk_size": str(config.build.chunk_size),
        "chunk_overlap": str(config.build.chunk_overlap),
    }
    if embed_configured:
        extra_meta["embed_model"] = e.model
        extra_meta["embed_dims"] = str(e.dims)
        extra_meta["embed_chars"] = str(e.embed_chars)

    resumed = False
    if not force and _resume_candidate(config, tmp_db):
        # Есть частично собранная tmp-БД: корпус/чанки готовы, эмбеддинг не дописан.
        resumed = True
        emit("embed", "Найдена незавершённая tmp-БД, возобновление эмбеддинга")
    else:
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
        total, links_total, chunks_total = _index_corpus(
            config, tmp_db, files, extra_meta, enqueue_embeds=embed_configured,
        )

    vectors = 0
    if embed_configured:
        vectors = _index_vectors(config, tmp_db, emit)

    if resumed:
        # В resume-режиме счётчики читаем из готовой tmp-БД до подмены.
        db = Database(tmp_db)
        conn = db.connect()
        try:
            total = conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
            links_total = conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
            chunks_total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        finally:
            conn.close()

    _atomic_replace(tmp_db, config.db_path)

    if config.build.cleanup:
        shutil.rmtree(corpus, ignore_errors=True)

    result.pages = total
    result.links = links_total
    result.chunks = chunks_total
    result.vectors = vectors
    result.embed_model = e.model if embed_configured else ""
    result.embed_dims = e.dims if embed_configured else 0
    result.embed_chars = e.embed_chars if embed_configured else 0
    result.chunk_size = config.build.chunk_size
    result.chunk_overlap = config.build.chunk_overlap
    result.threads = e.threads if embed_configured else 0
    result.duration_sec = round(time.time() - started, 2)
    done_msg = f"Готово: {total} страниц, {links_total} ссылок, {chunks_total} чанков"
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

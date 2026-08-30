"""Векторный поиск в SQLite (numpy, brute-force cosine) по чанкам.

Векторы хранятся в БД уже нормализованными (unit length) → косинус = скалярное
произведение. Матрица векторов кешируется в памяти процесса; инвалидация по
mtime БД (после пересборки).

Единица поиска — чанк. Результат несёт метаданные родителя (страницы) и номер
чанка. Дедупликация по родителю: не более ``max_chunks_per_page`` чанков одной
статьи в выдаче.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np

from v8help.search.base import SearchResult
from v8help.search.embedder import Embedder


class VectorBackend:
    def __init__(
        self,
        db_path: str | Path,
        embedder: Embedder,
        max_chunks_per_page: int = 2,
    ) -> None:
        self.db_path = Path(db_path)
        self.embedder = embedder
        self.max_chunks_per_page = max_chunks_per_page
        self._cache: tuple[tuple[str, float], np.ndarray, list[dict]] | None = None

    def _load(self) -> tuple[np.ndarray, list[dict]]:
        try:
            mtime = self.db_path.stat().st_mtime
        except OSError:
            mtime = 0.0
        key = (str(self.db_path), mtime)
        if self._cache is not None and self._cache[0] == key:
            return self._cache[1], self._cache[2]

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT c.id AS chunk_id, c.chunk_index, c.title AS chunk_title,"
                " p.filename, p.title, p.section, p.kind, v.vec,"
                " (SELECT COUNT(*) FROM chunks cc WHERE cc.page_id = p.id) AS total"
                " FROM vectors v"
                " JOIN chunks c ON c.id = v.chunk_id"
                " JOIN pages p ON p.id = c.page_id"
            ).fetchall()
        finally:
            conn.close()

        if not rows:
            self._cache = (key, np.empty((0, 0), dtype=np.float32), [])
            return self._cache[1], self._cache[2]

        vecs = np.array(
            [np.frombuffer(r["vec"], dtype=np.float32) for r in rows],
            dtype=np.float32,
        )
        metas = [
            {
                "filename": r["filename"],
                "title": r["title"],
                "section": r["section"],
                "kind": r["kind"],
                "chunk_id": r["chunk_id"],
                "chunk_index": r["chunk_index"],
                "chunk_title": r["chunk_title"],
                "total_chunks": r["total"],
            }
            for r in rows
        ]
        self._cache = (key, vecs, metas)
        return vecs, metas

    def search(
        self,
        query: str,
        limit: int = 10,
        section: str | None = None,
        kind: str | None = None,
    ) -> list[SearchResult]:
        vecs, metas = self._load()
        if not metas:
            return []

        q = np.asarray(self.embedder.embed_one(query), dtype=np.float32)
        norm = float(np.linalg.norm(q))
        if norm:
            q = q / norm

        mask = np.ones(len(metas), dtype=bool)
        if section:
            mask &= np.array([m["section"] == section for m in metas], dtype=bool)
        if kind:
            mask &= np.array([m["kind"] == kind for m in metas], dtype=bool)
        idx = np.flatnonzero(mask)
        if idx.size == 0:
            return []

        scores = vecs[idx] @ q
        top = np.argsort(-scores)[:limit * 4]

        out: list[SearchResult] = []
        seen: dict[str, int] = {}
        for k in top:
            i = int(idx[k])
            m = metas[i]
            fname = m["filename"]
            used = seen.get(fname, 0)
            if used >= self.max_chunks_per_page:
                continue
            seen[fname] = used + 1
            out.append(
                SearchResult(
                    id=fname,
                    title=m["title"],
                    snippet=m["chunk_title"],
                    source_path=fname,
                    section=m["section"],
                    kind=m["kind"],
                    score=float(scores[k]),
                    chunk_id=m["chunk_id"],
                    chunk_index=m["chunk_index"],
                    total_chunks=m["total_chunks"],
                    chunk_title=m["chunk_title"],
                )
            )
            if len(out) >= limit:
                break
        return out

"""Векторный поиск в SQLite (numpy, brute-force cosine).

Векторы хранятся в БД уже нормализованными (unit length) → косинус = скалярное
произведение. Матрица векторов кешируется в памяти процесса; инвалидация по
mtime БД (после пересборки).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np

from v8help.search.base import SearchResult
from v8help.search.embedder import Embedder


class VectorBackend:
    def __init__(self, db_path: str | Path, embedder: Embedder) -> None:
        self.db_path = Path(db_path)
        self.embedder = embedder
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
                "SELECT p.id, p.filename, p.title, p.section, p.kind, v.vec"
                " FROM vectors v JOIN pages p ON p.id = v.page_id"
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
                "id": r["id"],
                "filename": r["filename"],
                "title": r["title"],
                "section": r["section"],
                "kind": r["kind"],
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
        top = np.argsort(-scores)[:limit]

        out: list[SearchResult] = []
        for k in top:
            i = int(idx[k])
            m = metas[i]
            out.append(
                SearchResult(
                    id=m["filename"],
                    title=m["title"],
                    snippet=m["title"],
                    source_path=m["filename"],
                    section=m["section"],
                    kind=m["kind"],
                    score=float(scores[k]),
                )
            )
        return out

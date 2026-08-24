"""Гибридный поиск FTS + эмбеддинги (Reciprocal Rank Fusion)."""

from __future__ import annotations

from pathlib import Path

from v8help.search.base import SearchResult
from v8help.search.embedder import Embedder
from v8help.search.fts import FtsBackend
from v8help.search.ranking import reciprocal_rank_fusion
from v8help.search.vectors import VectorBackend


class HybridBackend:
    def __init__(self, db_path: str | Path, embedder: Embedder) -> None:
        self.fts = FtsBackend(db_path)
        self.vectors = VectorBackend(db_path, embedder)

    def search(
        self,
        query: str,
        limit: int = 10,
        section: str | None = None,
        kind: str | None = None,
    ) -> list[SearchResult]:
        n = max(limit * 4, 40)
        fts = self.fts.search(query, limit=n, section=section, kind=kind)
        vec = self.vectors.search(query, limit=n, section=section, kind=kind)
        return reciprocal_rank_fusion([fts, vec])[:limit]

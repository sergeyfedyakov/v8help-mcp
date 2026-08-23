"""Векторный поиск в SQLite (хранение и сравнение векторов)."""

from __future__ import annotations

from v8help.search.base import SearchResult


class VectorBackend:
    def search(
        self,
        query: str,
        limit: int = 10,
        section: str | None = None,
        kind: str | None = None,
    ) -> list[SearchResult]:
        raise NotImplementedError("Фаза 2: векторный поиск")

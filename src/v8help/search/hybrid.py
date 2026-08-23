"""Гибридный поиск FTS + эмбеддинги (RRF)."""

from __future__ import annotations

from v8help.search.base import SearchResult


class HybridBackend:
    def search(
        self,
        query: str,
        limit: int = 10,
        section: str | None = None,
        kind: str | None = None,
    ) -> list[SearchResult]:
        raise NotImplementedError("Фаза 2: гибридный поиск")

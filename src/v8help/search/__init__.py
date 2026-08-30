"""Подсистема поиска."""

from __future__ import annotations

from v8help.search.base import SearchBackend, SearchResult

__all__ = ["SearchBackend", "SearchResult", "make_backend"]


def _query_embedder(config):
    """Эмбеддер для поискового запроса: явный query → index (одна модель!)."""
    q = config.embedder_query
    if q.base_url and q.model:
        return q
    return config.embedder_index


def make_backend(config, db_path) -> SearchBackend:
    """Фабрика бэкенда по ``config.search.backend`` (fts|vectors|hybrid)."""
    from v8help.search.embedder import Embedder
    from v8help.search.fts import FtsBackend
    from v8help.search.hybrid import HybridBackend
    from v8help.search.vectors import VectorBackend

    mcp = config.search.max_chunks_per_page
    backend = (config.search.backend or "fts").lower()
    if backend == "vectors":
        return VectorBackend(db_path, Embedder(_query_embedder(config)), max_chunks_per_page=mcp)
    if backend == "hybrid":
        return HybridBackend(db_path, Embedder(_query_embedder(config)), max_chunks_per_page=mcp)
    return FtsBackend(db_path, max_chunks_per_page=mcp)

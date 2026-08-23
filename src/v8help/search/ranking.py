"""Ранжирование результатов (RRF, bm25)."""

from __future__ import annotations

from v8help.search.base import SearchResult


def reciprocal_rank_fusion(
    ranked_lists: list[list[SearchResult]],
    k: int = 60,
) -> list[SearchResult]:
    raise NotImplementedError("Фаза 2: RRF")

"""Ранжирование результатов (Reciprocal Rank Fusion)."""

from __future__ import annotations

from v8help.search.base import SearchResult


def _key(item: SearchResult) -> tuple:
    """Ключ слияния: чанк (если есть), иначе страница."""
    if item.chunk_id:
        return (item.id, item.chunk_id)
    return (item.id, 0)


def reciprocal_rank_fusion(
    ranked_lists: list[list[SearchResult]],
    k: int = 60,
) -> list[SearchResult]:
    """Сливает несколько ранжированных списков по RRF.

    При дубле по ``(id, chunk_id)`` оставляет первый встреченный объект (обычно
    из более «точного» списка, напр. FTS со сниппетом) и суммирует RRF-очки.
    """
    scores: dict[tuple, tuple[float, SearchResult]] = {}
    for lst in ranked_lists:
        for rank, item in enumerate(lst):
            key = _key(item)
            rrf = 1.0 / (k + rank + 1)
            if key in scores:
                cur, kept = scores[key]
                scores[key] = (cur + rrf, kept)
            else:
                scores[key] = (rrf, item)
    ranked = sorted(scores.values(), key=lambda pair: -pair[0])
    return [item for _, item in ranked]

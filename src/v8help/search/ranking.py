"""Ранжирование результатов (Reciprocal Rank Fusion)."""

from __future__ import annotations

from v8help.search.base import SearchResult


def reciprocal_rank_fusion(
    ranked_lists: list[list[SearchResult]],
    k: int = 60,
) -> list[SearchResult]:
    """Сливает несколько ранжированных списков по RRF.

    При дубле по ``id`` оставляет первый встреченный объект (обычно из более
    «точного» списка, напр. FTS со сниппетом) и суммирует RRF-очки.
    """
    scores: dict[str, tuple[float, SearchResult]] = {}
    for lst in ranked_lists:
        for rank, item in enumerate(lst):
            key = item.id
            rrf = 1.0 / (k + rank + 1)
            if key in scores:
                cur, kept = scores[key]
                scores[key] = (cur + rrf, kept)
            else:
                scores[key] = (rrf, item)
    ranked = sorted(scores.values(), key=lambda pair: -pair[0])
    return [item for _, item in ranked]

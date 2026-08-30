"""Базовые типы поиска: результат и протокол бэкенда."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class SearchResult:
    id: str
    title: str
    snippet: str
    source_path: str
    section: str = ""
    kind: str = ""
    score: float = 0.0
    chunk_id: int = 0
    chunk_index: int = 0
    total_chunks: int = 1
    chunk_title: str = ""


class SearchBackend(Protocol):
    def search(
        self,
        query: str,
        limit: int = 10,
        section: str | None = None,
        kind: str | None = None,
    ) -> list[SearchResult]: ...

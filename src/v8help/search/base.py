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


class SearchBackend(Protocol):
    def search(
        self,
        query: str,
        limit: int = 10,
        section: str | None = None,
        kind: str | None = None,
    ) -> list[SearchResult]: ...

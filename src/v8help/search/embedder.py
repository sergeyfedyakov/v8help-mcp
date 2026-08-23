"""Клиент эмбеддингов (OpenAI-совместимый /v1/embeddings)."""

from __future__ import annotations

from v8help.config import EmbedderConfig


class Embedder:
    def __init__(self, config: EmbedderConfig) -> None:
        self.config = config

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError("Фаза 2: эмбеддинги")

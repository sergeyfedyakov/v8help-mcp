"""Клиент эмбеддингов (OpenAI-совместимый ``/v1/embeddings``).

Работает через stdlib ``urllib`` (без requests). Поддерживает пакетный режим:
``input`` передаётся массивом строк, сервер (LM Studio, Ollama, …) возвращает
столько же векторов.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable

from v8help.config import EmbedderConfig


class EmbedderError(RuntimeError):
    pass


BatchProgress = Callable[[int, int], None]


class Embedder:
    def __init__(self, config: EmbedderConfig, timeout: float = 120.0) -> None:
        self.config = config
        self.timeout = timeout

    @property
    def base_url(self) -> str:
        return (self.config.base_url or "").rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self.config.base_url and self.config.model)

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.config.api_key:
            h["Authorization"] = f"Bearer {self.config.api_key}"
        return h

    def _request(self, payload: dict) -> dict:
        url = f"{self.base_url}/embeddings"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST", headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            raise EmbedderError(f"HTTP {exc.code} от {url}: {body}") from exc
        except urllib.error.URLError as exc:
            raise EmbedderError(f"Недоступен {url}: {exc.reason}") from exc

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Эмбеддинги для списка текстов (один запрос, батч)."""
        if not texts:
            return []
        if not self.configured:
            raise EmbedderError("Эмбеддер не настроен (нет model/base_url)")
        data = self._request({"model": self.config.model, "input": texts})
        vecs = [item["embedding"] for item in data.get("data", [])]
        if len(vecs) != len(texts):
            raise EmbedderError(
                f"Ожидалось {len(texts)} векторов, получено {len(vecs)}"
            )
        if self.config.dims and vecs and len(vecs[0]) != self.config.dims:
            raise EmbedderError(
                f"Размерность {len(vecs[0])} != ожидаемой {self.config.dims}"
            )
        return vecs

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]

    def embed_batches(
        self,
        texts: list[str],
        batch_size: int | None = None,
        on_batch: BatchProgress | None = None,
    ) -> list[list[float]]:
        """Батчевая индексация с прогрессом ``on_batch(done, total)``."""
        bs = batch_size or self.config.batch_size or 64
        out: list[list[float]] = []
        for i in range(0, len(texts), bs):
            out.extend(self.embed(texts[i : i + bs]))
            if on_batch is not None:
                on_batch(min(len(texts), i + bs), len(texts))
        return out

    def models(self) -> list[str]:
        """Список id моделей (``GET {base}/models``), для дискавери."""
        url = f"{self.base_url}/models"
        req = urllib.request.Request(url, method="GET", headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise EmbedderError(f"models недоступен: {exc}") from exc
        return [m.get("id", "") for m in data.get("data", []) if m.get("id")]

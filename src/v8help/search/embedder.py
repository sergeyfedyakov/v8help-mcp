"""Клиент эмбеддингов: OpenAI-совместимый ``/v1/embeddings`` или HF native pipeline.

Формат запроса задаётся явно в конфиге ``provider``:

- ``"openai"`` (по умолчанию) — LM Studio, Ollama, облака: ``POST {base}/embeddings``
  с ``{"model", "input": [...]}``, ответ ``{"data": [{"embedding": [...]}]}``.
- ``"hf"`` — Hugging Face Inference native: ``POST {base}/{model}/pipeline/feature-extraction``
  с ``{"inputs": [...]}``, ответ — список векторов.

Работает через stdlib ``urllib`` (без requests). Поддерживает пакетный режим.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
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

    @property
    def native_hf(self) -> bool:
        """Native Hugging Face pipeline API (provider="hf")."""
        return (self.config.provider or "openai").strip().lower() == "hf"

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.config.api_key:
            h["Authorization"] = f"Bearer {self.config.api_key}"
        return h

    def _post_json(self, url: str, payload: dict) -> dict | list:
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
        if self.native_hf:
            url = (
                f"{self.base_url}/{self.config.model}/pipeline/feature-extraction"
            )
            data = self._post_json(url, {"inputs": texts})
            if isinstance(data, list) and data and isinstance(data[0], list):
                vecs = data
            elif isinstance(data, list) and data and isinstance(data[0], float):
                vecs = [data]
            else:
                raise EmbedderError(
                    f"Неожиданный формат ответа {type(data).__name__} от {url}"
                )
        else:
            url = f"{self.base_url}/embeddings"
            data = self._post_json(url, {"model": self.config.model, "input": texts})
            if not isinstance(data, dict):
                raise EmbedderError(f"Неожиданный формат ответа от {url}")
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
        threads: int = 1,
    ) -> list[list[float]]:
        """Батчевая индексация с прогрессом ``on_batch(done, total)``.

        При ``threads > 1`` батчи распределяются между пулом потоков (LM Studio
        обрабатывает параллельные запросы), прогресс — через потокобезопасный
        счётчик. Результаты возвращаются в исходном порядке.
        """
        bs = batch_size or self.config.batch_size or 64
        n = len(texts)
        if n == 0:
            return []
        if threads <= 1:
            return self._embed_sequential(texts, bs, on_batch)

        out: list[list[float]] = [None] * n  # type: ignore[list-item]
        lock = threading.Lock()
        done = 0
        nthreads = max(1, min(int(threads), n))
        chunk = (n + nthreads - 1) // nthreads

        def worker(start: int, end: int) -> None:
            nonlocal done
            i = start
            while i < end:
                vecs = self.embed(texts[i : i + bs])
                with lock:
                    out[i : i + bs] = vecs
                    done += len(vecs)
                    if on_batch is not None:
                        on_batch(done, n)
                i += bs

        with ThreadPoolExecutor(max_workers=nthreads) as pool:
            futures = [
                pool.submit(worker, s, min(n, s + chunk))
                for s in range(0, n, chunk)
            ]
            for f in futures:
                f.result()
        return out

    def _embed_sequential(
        self,
        texts: list[str],
        batch_size: int,
        on_batch: BatchProgress | None,
    ) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            out.extend(self.embed(texts[i : i + batch_size]))
            if on_batch is not None:
                on_batch(min(len(texts), i + batch_size), len(texts))
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

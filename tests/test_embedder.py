import json
import urllib.request

import pytest

from v8help.config import EmbedderConfig
from v8help.search.embedder import Embedder, EmbedderError


class _FakeResp:
    def __init__(self, payload: dict):
        self._data = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._data


def test_embed_batch(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return _FakeResp({"data": [{"embedding": [1.0, 2.0]}, {"embedding": [3.0, 4.0]}]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    emb = Embedder(EmbedderConfig(model="m", base_url="http://localhost:1234/v1"))
    out = emb.embed(["a", "b"])
    assert out == [[1.0, 2.0], [3.0, 4.0]]
    assert captured["payload"]["model"] == "m"
    assert captured["payload"]["input"] == ["a", "b"]


def test_embed_dims_mismatch(monkeypatch):
    def fake_urlopen(req, timeout=None):
        return _FakeResp({"data": [{"embedding": [1.0, 2.0]}]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    emb = Embedder(
        EmbedderConfig(model="m", base_url="http://x/v1", dims=3)
    )
    with pytest.raises(EmbedderError):
        emb.embed(["a"])


def test_embed_batches_splits(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=None):
        payload = json.loads(req.data.decode("utf-8"))
        calls.append(payload["input"])
        return _FakeResp(
            {"data": [{"embedding": [float(i)]} for i in range(len(payload["input"]))]}
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    emb = Embedder(
        EmbedderConfig(model="m", base_url="http://x/v1", batch_size=2)
    )
    out = emb.embed_batches(["a", "b", "c"])
    assert calls == [["a", "b"], ["c"]]
    assert len(out) == 3


def test_models(monkeypatch):
    def fake_urlopen(req, timeout=None):
        return _FakeResp(
            {"data": [{"id": "text-embedding-qwen3-embedding-0.6b"}, {"id": "gpt"}]}
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    emb = Embedder(EmbedderConfig(model="m", base_url="http://x/v1"))
    assert emb.models() == ["text-embedding-qwen3-embedding-0.6b", "gpt"]


def test_not_configured():
    emb = Embedder(EmbedderConfig())
    assert not emb.configured
    with pytest.raises(EmbedderError):
        emb.embed(["a"])

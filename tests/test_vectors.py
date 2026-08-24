import numpy as np

from v8help.db import Database
from v8help.search.base import SearchResult
from v8help.search.hybrid import HybridBackend
from v8help.search.ranking import reciprocal_rank_fusion
from v8help.search.vectors import VectorBackend


class _FixedEmbedder:
    def __init__(self, vec=(0, 1, 0, 0)):
        self._vec = list(vec)

    def embed_one(self, text):
        return list(self._vec)


def _unit(*vals):
    a = np.array(vals, dtype=np.float32)
    n = np.linalg.norm(a)
    return (a / n if n else a).tobytes()


def _build(tmp_path):
    db = Database(tmp_path / "v.db")
    conn = db.connect()
    db.reset(conn)
    pids = {}
    for fn, sec, kind, body in [
        ("Alpha", "objects", "page", "alpha text"),
        ("Beta", "objects", "page", "beta text"),
        ("Gamma", "lang", "member", "gamma text"),
    ]:
        pids[fn] = db.insert_page(conn, fn, fn, sec, kind, "", "", body, fn, body)
    db.insert_vector(conn, pids["Alpha"], _unit(1, 0, 0, 0))
    db.insert_vector(conn, pids["Beta"], _unit(0, 1, 0, 0))
    db.insert_vector(conn, pids["Gamma"], _unit(0, 0, 1, 0))
    conn.commit()
    conn.close()
    return db.path


def test_vector_search_ordering(tmp_path):
    backend = VectorBackend(_build(tmp_path), _FixedEmbedder())
    results = backend.search("query", limit=2)
    assert [r.id for r in results] == ["Beta", "Alpha"]


def test_vector_search_section_filter(tmp_path):
    backend = VectorBackend(_build(tmp_path), _FixedEmbedder())
    results = backend.search("query", limit=5, section="lang")
    assert [r.id for r in results] == ["Gamma"]


def test_hybrid_returns_fused(tmp_path):
    backend = HybridBackend(_build(tmp_path), _FixedEmbedder())
    results = backend.search("beta", limit=5)
    assert any(r.id == "Beta" for r in results)


def test_rrf_fusion_order():
    a = [SearchResult(id=i, title=i, snippet=i, source_path=i) for i in "xyz"]
    b = [SearchResult(id="z", title="z", snippet="z", source_path="z"),
         SearchResult(id="x", title="x", snippet="x", source_path="x")]
    fused = reciprocal_rank_fusion([a, b])
    assert [r.id for r in fused] == ["x", "z", "y"]


def test_rrf_keeps_first_item():
    a = [SearchResult(id="x", title="fts", snippet="fts", source_path="x")]
    b = [SearchResult(id="x", title="vec", snippet="vec", source_path="x")]
    fused = reciprocal_rank_fusion([a, b])
    assert fused[0].title == "fts"

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
    for fn, sec, kind, body in [
        ("Alpha", "objects", "page", "alpha text"),
        ("Beta", "objects", "page", "beta text"),
        ("Gamma", "lang", "member", "gamma text"),
    ]:
        pid = db.insert_page(conn, fn, fn, sec, kind, "", "", body, fn, body)
        cid = db.insert_chunk(conn, pid, 0, fn, body)
        db.insert_vector(conn, cid, _unit(1, 0, 0, 0) if fn == "Alpha" else _unit(0, 1, 0, 0) if fn == "Beta" else _unit(0, 0, 1, 0))
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


def _build_multi_chunk(tmp_path):
    """Одна страница с несколькими чанками, два одинаковых вектора."""
    db = Database(tmp_path / "v2.db")
    conn = db.connect()
    db.reset(conn)
    pid = db.insert_page(conn, "Big", "Big", "objects", "page", "", "",
                         "big" * 2000, "Big", "big")
    ids = [db.insert_chunk(conn, pid, i, "Big", f"chunk {i} big text") for i in range(4)]
    for cid in ids:
        db.insert_vector(conn, cid, _unit(1, 0, 0, 0))
    pid2 = db.insert_page(conn, "Other", "Other", "objects", "page", "", "",
                          "other", "Other", "other")
    cid2 = db.insert_chunk(conn, pid2, 0, "Other", "other text")
    db.insert_vector(conn, cid2, _unit(0, 1, 0, 0))
    conn.commit()
    conn.close()
    return db.path


def test_vector_dedup_per_page(tmp_path):
    backend = VectorBackend(_build_multi_chunk(tmp_path), _FixedEmbedder())
    results = backend.search("query", limit=10)
    ids = [r.id for r in results]
    # Other имеет выше косинус (вектор совпадает с запросом), Big ниже
    assert ids.count("Big") == 2  # max_chunks_per_page по умолчанию
    assert ids[-1] == "Big"
    assert len(ids) == 3  # 2 чанка Big + 1 Other, несмотря на 4 вектора Big


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

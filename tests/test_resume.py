"""Resume-механика: план embed_queue, инкрементальная запись, возобновление."""

from pathlib import Path

import pytest

from v8help.config import Config
from v8help.db import Database
from v8help.indexer import (
    _index_corpus,
    _index_vectors,
    _resume_candidate,
    run_build,
)


class FakeEmbedder:
    """Фейковый эмбеддер: детерминированный вектор, опциональное падение.

    ``fail_after`` — после скольких успешных вызовов ``embed_batches`` бросить
    RuntimeError (имитация сбоя/рестарта в середине эмбеддинга).
    """

    def __init__(self, config, fail_after: int | None = None):
        self.config = config
        self.fail_after = fail_after
        self.calls = 0

    def embed_batches(self, texts, threads=1, on_batch=None):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise RuntimeError("embedder crashed")
        if on_batch:
            on_batch(len(texts), len(texts))
        return [[0.25, 0.5, 0.75, 0.0] for _ in texts]


def _make_corpus(tmp_path: Path, n: int = 20) -> Path:
    corpus = tmp_path / "corpus"
    corpus.mkdir(exist_ok=True)
    for i in range(n):
        (corpus / f"lang__def_Page{i}.md").write_text(
            f"# Страница {i}\n\n" + "\n".join(
                f"- Пункт {j}: текст параметра номер {j}."
                for j in range(120)
            ),
            encoding="utf-8",
        )
    return corpus


def _config(tmp_path: Path) -> Config:
    cfg = Config()
    cfg.corpus_dir = _make_corpus(tmp_path)
    cfg.db_path = tmp_path / "resume.db"
    cfg.books = []
    cfg.build.chunk_size = 1500
    cfg.build.chunk_overlap = 200
    cfg.embedder_index.model = "test-model"
    cfg.embedder_index.base_url = "http://embedder"
    cfg.embedder_index.dims = 4
    cfg.embedder_index.batch_size = 2
    cfg.embedder_index.threads = 1
    return cfg


def _extra_meta(cfg: Config) -> dict:
    e = cfg.embedder_index
    return {
        "chunk_size": str(cfg.build.chunk_size),
        "chunk_overlap": str(cfg.build.chunk_overlap),
        "embed_model": e.model,
        "embed_dims": str(e.dims),
        "embed_chars": str(e.embed_chars),
    }


def _build_partial(cfg: Config, tmp: Path) -> int:
    """Индексация корпуса в tmp-БД (как делает run_build): возвращает число чанков."""
    total, _links, chunks = _index_corpus(
        cfg, tmp, sorted(cfg.corpus_dir.glob("*.md")),
        extra_meta=_extra_meta(cfg), enqueue_embeds=True,
    )
    assert total == 20
    return chunks


def _counts(db_path: Path) -> tuple[int, int]:
    db = Database(db_path)
    conn = db.connect()
    try:
        pending = conn.execute("SELECT COUNT(*) FROM embed_queue").fetchone()[0]
        vectors = conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
    finally:
        conn.close()
    return pending, vectors


def _patch_embedder(monkeypatch, embedder):
    import v8help.indexer as idx_mod
    monkeypatch.setattr(idx_mod, "Embedder", lambda e: embedder)


def test_index_corpus_fills_plan(tmp_path):
    cfg = _config(tmp_path)
    tmp = tmp_path / "partial.db"
    chunks = _build_partial(cfg, tmp)
    pending, vectors = _counts(tmp)
    assert chunks > 4
    assert pending == chunks
    assert vectors == 0


def test_index_vectors_writes_incrementally_and_removes_plan(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    tmp = tmp_path / "partial.db"
    chunks = _build_partial(cfg, tmp)

    # Суперпачка = max(bs*threads*2, bs) = max(2*1*2, 2) = 4 чанка.
    failing = FakeEmbedder(cfg.embedder_index, fail_after=1)
    _patch_embedder(monkeypatch, failing)
    with pytest.raises(RuntimeError, match="embedder crashed"):
        _index_vectors(cfg, tmp, lambda stage, msg: None)

    pending, vectors = _counts(tmp)
    assert vectors == 4  # ровно одна суперпачка закоммичена
    assert pending == chunks - 4
    assert _resume_candidate(cfg, tmp) is True

    # Возобновление: доделываем оставшееся, не трогая готовые векторы.
    rest = FakeEmbedder(cfg.embedder_index)
    _patch_embedder(monkeypatch, rest)
    _index_vectors(cfg, tmp, lambda stage, msg: None)

    pending, vectors = _counts(tmp)
    assert pending == 0
    assert vectors == chunks
    # На повторном проходе эмбеддер НЕ пересчитывал уже готовые чанки:
    # после падения осталось не больше ceil((chunks-4)/4) суперпачек.
    assert rest.calls * 4 >= chunks - 4
    assert _resume_candidate(cfg, tmp) is False


def test_resume_candidate_false_without_embedder(tmp_path):
    cfg = _config(tmp_path)
    cfg.embedder_index.model = ""
    tmp = tmp_path / "partial.db"
    _index_corpus(cfg, tmp, sorted(cfg.corpus_dir.glob("*.md")),
                  extra_meta=_extra_meta(cfg), enqueue_embeds=False)
    assert _resume_candidate(cfg, tmp) is False


def test_resume_candidate_requires_matching_chunk_size(tmp_path):
    cfg = _config(tmp_path)
    tmp = tmp_path / "partial.db"
    _build_partial(cfg, tmp)
    cfg2 = _config(tmp_path)
    cfg2.build.chunk_size = 999
    assert _resume_candidate(cfg2, tmp) is False


def test_run_build_resumes_partial(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    tmp = cfg.db_path.with_name(cfg.db_path.name + ".tmp")
    chunks = _build_partial(cfg, tmp)

    # Первый проход падает на второй суперпачке.
    failing = FakeEmbedder(cfg.embedder_index, fail_after=1)
    _patch_embedder(monkeypatch, failing)
    with pytest.raises(RuntimeError, match="embedder crashed"):
        _index_vectors(cfg, tmp, lambda stage, msg: None)

    # run_build(force=False) должен обнаружить tmp и доделать эмбеддинг,
    # не пересобирая корпус и не пересоздавая tmp-БД.
    done = FakeEmbedder(cfg.embedder_index)
    _patch_embedder(monkeypatch, done)
    result = run_build(cfg, force=False)

    assert result.pages == 20
    assert result.vectors == chunks
    # Осиротевший tmp исчез (был атомарно подменён).
    assert not tmp.exists()
    pending, vectors = _counts(cfg.db_path)
    assert pending == 0
    assert vectors == chunks

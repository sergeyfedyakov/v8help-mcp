"""Интеграционные тесты: чанкование в индексе, поиск по чанкам, get_page с chunk."""

from pathlib import Path

import sqlite3

from v8help.config import Config
from v8help.db import Database
from v8help.indexer import build_index
from v8help.search.fts import FtsBackend
from v8help.server import McpServer


def _make_corpus(tmp_path) -> Path:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    # Короткая статья — один чанк.
    (corpus / "lang__def_String.md").write_text(
        "# Строка\n\nЗначения данного типа содержат строку Unicode.\n",
        encoding="utf-8",
    )
    # Длинная статья (>1500) — несколько чанков, без markdown-заголовков.
    long_body = "# Глобальный контекст.ДлиннаяФункция\n\n" + "\n".join(
        f"- Пункт {i}: описание параметра номер {i}, тип Число."
        for i in range(120)
    )
    (corpus / "Глобальный_контекст.ДлиннаяФункция.md").write_text(
        long_body, encoding="utf-8"
    )
    return corpus


def _build(tmp_path, chunk_size: int = 1500, chunk_overlap: int = 200) -> Config:
    config = Config()
    config.corpus_dir = _make_corpus(tmp_path)
    config.db_path = tmp_path / "chunk.db"
    config.books = []
    config.build.chunk_size = chunk_size
    config.build.chunk_overlap = chunk_overlap
    build_index(config)
    return config


def test_long_page_split_into_chunks(tmp_path):
    _build(tmp_path)
    db = Database(tmp_path / "chunk.db")
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT p.id FROM pages p WHERE p.filename='Глобальный_контекст.ДлиннаяФункция'"
        ).fetchone()
        chunks = conn.execute(
            "SELECT chunk_index, chars, src, prev_id, next_id FROM chunks"
            " WHERE page_id=? ORDER BY chunk_index", (row["id"],)
        ).fetchall()
        assert len(chunks) > 1
        assert chunks[0]["src"] == "chunk"
        # метаданные соседей
        assert chunks[0]["next_id"] is not None
        assert chunks[0]["prev_id"] is None
        assert chunks[-1]["prev_id"] is not None
        assert chunks[-1]["next_id"] is None
        # тело чанков не превышает целевой размер + одна строка
        assert all(c["chars"] <= 1500 + 80 for c in chunks)
        # страница целиком сохранена
        page = conn.execute(
            "SELECT length(body) AS ln FROM pages WHERE filename='Глобальный_контекст.ДлиннаяФункция'"
        ).fetchone()
        assert page["ln"] > 1500
    finally:
        conn.close()


def test_search_finds_chunk_of_long_page(tmp_path):
    _build(tmp_path)
    backend = FtsBackend(tmp_path / "chunk.db")
    results = backend.search("Пункт 95", limit=10)
    assert results
    r = results[0]
    assert r.id == "Глобальный_контекст.ДлиннаяФункция"
    assert r.total_chunks > 1
    assert r.chunk_index >= 0
    # найденный чанк реально содержит искомый фрагмент
    conn = sqlite3.connect(tmp_path / "chunk.db")
    body = conn.execute(
        "SELECT body FROM chunks WHERE id=?", (r.chunk_id,)
    ).fetchone()[0]
    conn.close()
    assert "Пункт 95" in body


def test_fts_no_duplicate_chunks(tmp_path):
    """Один чанк, найденный и по title, и по body, не должен дублироваться."""
    _build(tmp_path)
    backend = FtsBackend(tmp_path / "chunk.db")
    results = backend.search("ДлиннаяФункция", limit=10)
    ids = [(r.id, r.chunk_id) for r in results]
    assert len(ids) == len(set(ids)), ids


def test_fts_dedup_limit_per_page(tmp_path):
    """Из длинной статьи в топе не больше max_chunks_per_page чанков."""
    _build(tmp_path)
    backend = FtsBackend(tmp_path / "chunk.db", max_chunks_per_page=2)
    results = backend.search("Пункт", limit=10)
    count = sum(1 for r in results if r.id == "Глобальный_контекст.ДлиннаяФункция")
    assert 0 < count <= 2


def test_get_page_long_returns_first_chunk_only(tmp_path):
    config = _build(tmp_path)
    server = McpServer(config)
    resp = server.handle_message({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {
            "name": "get_page",
            "arguments": {"id": "Глобальный_контекст.ДлиннаяФункция"},
        },
    })
    assert resp["result"]["isError"] is False
    import json
    data = json.loads(resp["result"]["content"][0]["text"])
    assert data["total_chunks"] > 1
    assert data["truncated"] is True
    assert data["chunk_index"] == 0
    assert len(data["body"]) <= 1500 + 80
    assert len(data["chunks"]) == data["total_chunks"]


def test_get_page_chunk_n(tmp_path):
    config = _build(tmp_path)
    server = McpServer(config)
    import json

    def call(iid, args):
        resp = server.handle_message({
            "jsonrpc": "2.0", "id": iid, "method": "tools/call",
            "params": {"name": "get_page", "arguments": args},
        })
        return json.loads(resp["result"]["content"][0]["text"])

    data0 = call(1, {"id": "Глобальный_контекст.ДлиннаяФункция", "chunk": 0})
    data1 = call(2, {"id": "Глобальный_контекст.ДлиннаяФункция", "chunk": 1})
    assert data0["chunk_index"] == 0
    assert data1["chunk_index"] == 1
    assert data0["body"] != data1["body"]
    # выход за границы — ошибка
    bad = server.handle_message({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {
            "name": "get_page",
            "arguments": {"id": "Глобальный_контекст.ДлиннаяФункция", "chunk": 999},
        },
    })
    assert bad["result"]["isError"] is True


def test_sqlite_schema_has_chunks(tmp_path):
    _build(tmp_path)
    conn = sqlite3.connect(tmp_path / "chunk.db")
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    conn.close()
    assert "chunks" in tables
    assert "chunks_fts" in tables
    assert "vectors" in tables


def _make_desc_corpus(tmp_path) -> Path:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    # A: термин в заголовке (вес 4).
    (corpus / "ЧтениеXML.Сбрасывает.md").write_text(
        "# ЧтениеXML.Сбрасывает\n\nСинтаксис:\n\nСбрасывает()\n\nОписание:\n\nМетод."
        "\n\nДоступность:\n\nВсе клиенты.\n",
        encoding="utf-8",
    )
    # B: термин только в описании (вес 2).
    (corpus / "ЧтениеXML.Начать.md").write_text(
        "# ЧтениеXML.Начать\n\nСинтаксис:\n\nНачать()\n\nОписание:\n\nСбрасывает "
        "поток чтения XML.\n\nДоступность:\n\nВсе клиенты.\n",
        encoding="utf-8",
    )
    # C: термин только в теле (вес 1).
    (corpus / "ЧтениеXML.Пропустить.md").write_text(
        "# ЧтениеXML.Пропустить\n\nПропустить()\n\nТело статьи: сбрасывает "
        "внутренние указатели.\n",
        encoding="utf-8",
    )
    return corpus


def test_fts_description_ranking(tmp_path):
    """Заголовок важнее описания, описание важнее тела (веса 4/2/1)."""
    config = Config()
    config.corpus_dir = _make_desc_corpus(tmp_path)
    config.db_path = tmp_path / "rank.db"
    config.books = []
    build_index(config)

    backend = FtsBackend(tmp_path / "rank.db")
    results = backend.search("сбрасывает", limit=10)
    order = [r.id for r in results]
    assert order.index("ЧтениеXML.Сбрасывает") < order.index("ЧтениеXML.Начать") \
        < order.index("ЧтениеXML.Пропустить"), order


def test_fts_description_in_fts_column(tmp_path):
    """description попадает в chunks_fts и находится поиском."""
    config = Config()
    config.corpus_dir = _make_desc_corpus(tmp_path)
    config.db_path = tmp_path / "rank2.db"
    config.books = []
    build_index(config)

    # B находится по слову из описания, отсутствующему в теле.
    backend = FtsBackend(tmp_path / "rank2.db")
    ids = {r.id for r in backend.search("поток чтения", limit=10)}
    assert "ЧтениеXML.Начать" in ids

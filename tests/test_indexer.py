from v8help.config import Config
from v8help.db import Database
from v8help.indexer import build_index
from v8help.search.fts import FtsBackend


def _make_corpus(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "lang__def_String.md").write_text(
        "# Строка\n\nЗначения данного типа содержат строку Unicode.\n",
        encoding="utf-8",
    )
    (corpus / "НастройкаПериода.ПолучитьДатуНачала.md").write_text(
        "# НастройкаПериода.ПолучитьДатуНачала\n\nПолучает дату начала периода.\n"
        "См. также [Строка](lang__def_String.md).\n",
        encoding="utf-8",
    )
    (corpus / "query__BETWEEN.md").write_text(
        "# Оператор МЕЖДУ\n\nПроверяет вхождение значения в диапазон.\n",
        encoding="utf-8",
    )
    (corpus / "Глобальный_контекст.СтрНайтиПоРегулярномуВыражению.md").write_text(
        "# Глобальный контекст.СтрНайтиПоРегулярномуВыражению\n\n"
        "Выполняет поиск строки по регулярному выражению.\n",
        encoding="utf-8",
    )
    return corpus


def test_index_and_search_roundtrip(tmp_path):
    corpus = _make_corpus(tmp_path)
    db_path = tmp_path / "test.db"
    config = Config()
    config.corpus_dir = corpus
    config.db_path = db_path
    config.books = []

    assert build_index(config) == 0
    assert Database(db_path).exists()

    backend = FtsBackend(db_path)

    ids = [r.id for r in backend.search("строка", limit=10)]
    assert "lang__def_String" in ids

    ids = [r.id for r in backend.search("ПолучитьДату", limit=10)]
    assert "НастройкаПериода.ПолучитьДатуНачала" in ids

    ids = [r.id for r in backend.search("между", limit=10)]
    assert "query__BETWEEN" in ids

    res = backend.search("между", section="query", limit=10)
    assert res and res[0].id == "query__BETWEEN"

    res = backend.search("строка", section="lang", limit=10)
    assert res and res[0].id == "lang__def_String"


def test_split_identifier_partial_word(tmp_path):
    corpus = _make_corpus(tmp_path)
    db_path = tmp_path / "test.db"
    config = Config()
    config.corpus_dir = corpus
    config.db_path = db_path
    config.books = []
    build_index(config)

    backend = FtsBackend(db_path)

    ids = [r.id for r in backend.search("регулярному", limit=10)]
    assert "Глобальный_контекст.СтрНайтиПоРегулярномуВыражению" in ids

    ids = [r.id for r in backend.search("СтрНайтиПоРегулярномуВыражению", limit=10)]
    assert "Глобальный_контекст.СтрНайтиПоРегулярномуВыражению" in ids


def test_get_page_and_related(tmp_path):
    corpus = _make_corpus(tmp_path)
    db_path = tmp_path / "test.db"
    config = Config()
    config.corpus_dir = corpus
    config.db_path = db_path
    config.books = []
    build_index(config)

    conn = Database(db_path).connect()
    try:
        row = conn.execute(
            "SELECT * FROM pages WHERE filename='НастройкаПериода.ПолучитьДатуНачала'"
        ).fetchone()
        assert row is not None
        assert row["section"] == "objects"
        assert row["kind"] == "member"
        assert "дату начала" in row["body"]

        links = conn.execute(
            "SELECT dst FROM links WHERE src='НастройкаПериода.ПолучитьДатуНачала'"
        ).fetchall()
        assert [r["dst"] for r in links] == ["lang__def_String"]
    finally:
        conn.close()


def test_cleanup_removes_corpus(tmp_path):
    corpus = _make_corpus(tmp_path)
    db_path = tmp_path / "test.db"
    config = Config()
    config.corpus_dir = corpus
    config.db_path = db_path
    config.books = []
    config.build.cleanup = True

    build_index(config)
    assert not corpus.exists()
    assert Database(db_path).exists()

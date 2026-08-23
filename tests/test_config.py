from v8help.config import Config


def test_load_config(tmp_path):
    cfg_path = tmp_path / "v8help.toml"
    cfg_path.write_text(
        """\
corpus_dir = "data/converted"
db_path = "data/v8help.db"
books = ["shcntx_ru", "shlang_ru"]
include_english = false

[embedder.index]
model = "multilingual-e5-small"
dims = 384

[embedder.query]
model = "multilingual-e5-small"
dims = 384

[search]
backend = "fts"
limit = 20
""",
        encoding="utf-8",
    )
    cfg = Config.load(cfg_path)
    assert cfg.books == ["shcntx_ru", "shlang_ru"]
    assert cfg.search.backend == "fts"
    assert cfg.search.limit == 20
    assert cfg.embedder_index.model == "multilingual-e5-small"
    assert cfg.embedder_index.dims == 384
    assert cfg.embedder_query.dims == 384
    assert cfg.include_english is False


def test_defaults():
    cfg = Config()
    assert cfg.search.backend == "fts"
    assert cfg.search.limit == 10
    assert cfg.books == ["shcntx_ru", "shlang_ru", "shquery_ru", "shclang_ru"]
    assert cfg.embedder_index.model == ""
    assert cfg.embedder_index.dims == 0

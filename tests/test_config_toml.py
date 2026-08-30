from v8help.config import Config, config_to_toml


def _configured() -> Config:
    cfg = Config()
    cfg.search.backend = "hybrid"
    cfg.search.limit = 25
    cfg.embedder_index.model = "text-embedding-qwen3-embedding-0.6b"
    cfg.embedder_index.base_url = "http://localhost:1234/v1"
    cfg.embedder_index.dims = 1024
    cfg.embedder_index.batch_size = 32
    cfg.embedder_index.embed_chars = 750
    cfg.lang = "ru"
    cfg.books = ["shcntx_ru", "shlang_ru"]
    return cfg


def test_config_roundtrip(tmp_path):
    toml_text = config_to_toml(_configured().to_dict())
    p = tmp_path / "v8help.toml"
    p.write_text(toml_text, encoding="utf-8")
    loaded = Config.load(p)
    assert loaded.search.backend == "hybrid"
    assert loaded.search.limit == 25
    assert loaded.embedder_index.model == "text-embedding-qwen3-embedding-0.6b"
    assert loaded.embedder_index.base_url == "http://localhost:1234/v1"
    assert loaded.embedder_index.dims == 1024
    assert loaded.embedder_index.batch_size == 32
    assert loaded.embedder_index.embed_chars == 750
    assert loaded.books == ["shcntx_ru", "shlang_ru"]


def test_config_defaults_roundtrip(tmp_path):
    toml_text = config_to_toml(Config().to_dict())
    p = tmp_path / "v8help.toml"
    p.write_text(toml_text, encoding="utf-8")
    loaded = Config.load(p)
    assert loaded.search.backend == "fts"
    assert loaded.embedder_index.model == ""
    assert loaded.embedder_index.batch_size == 64
    assert loaded.embedder_index.embed_chars == 500
    assert loaded.embedder_index.threads == 2
    assert loaded.search.max_chunks_per_page == 2
    assert loaded.build.chunk_size == 1500
    assert loaded.build.chunk_overlap == 200


def test_config_new_fields_roundtrip(tmp_path):
    cfg = Config()
    cfg.embedder_index.threads = 4
    cfg.search.max_chunks_per_page = 1
    cfg.build.chunk_size = 2000
    cfg.build.chunk_overlap = 250
    toml_text = config_to_toml(cfg.to_dict())
    p = tmp_path / "v8help.toml"
    p.write_text(toml_text, encoding="utf-8")
    loaded = Config.load(p)
    assert loaded.embedder_index.threads == 4
    assert loaded.search.max_chunks_per_page == 1
    assert loaded.build.chunk_size == 2000
    assert loaded.build.chunk_overlap == 250


def test_toml_embedder_query_section(tmp_path):
    cfg = Config()
    cfg.embedder_query.base_url = "http://localhost:11434/v1"
    cfg.embedder_query.model = "bge-m3"
    toml_text = config_to_toml(cfg.to_dict())
    p = tmp_path / "v8help.toml"
    p.write_text(toml_text, encoding="utf-8")
    loaded = Config.load(p)
    assert loaded.embedder_query.base_url == "http://localhost:11434/v1"
    assert loaded.embedder_query.model == "bge-m3"

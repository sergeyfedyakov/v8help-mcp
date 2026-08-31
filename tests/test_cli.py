from v8help.cli import _load_config, _serve, build_parser
from v8help.config import Config


def test_parser_search():
    args = build_parser().parse_args(["search", "запрос"])
    assert args.command == "search"
    assert args.query == "запрос"


def test_parser_build():
    args = build_parser().parse_args(["build", "--sources", "shcntx_ru", "shlang_ru"])
    assert args.command == "build"
    assert args.sources == ["shcntx_ru", "shlang_ru"]


def test_serve_uses_env_config(tmp_path, monkeypatch):
    """serve должен уважать V8HELP_CONFIG (иначе в контейнере base=PROJECT_ROOT)."""
    toml = tmp_path / "c.toml"
    toml.write_text(f'db_path = "{(tmp_path / "x.db").as_posix()}"\n')
    monkeypatch.setenv("V8HELP_CONFIG", str(toml))
    monkeypatch.delenv("V8HELP_DB_PATH", raising=False)

    from v8help import server

    captured = {}

    def fake_serve(config, config_path, http=False, host=None, port=None):
        captured["config_path"] = config_path
        captured["db_path"] = str(config.db_path).replace("\\", "/")
        return 0

    monkeypatch.setattr(server, "serve", fake_serve)
    _serve(build_parser().parse_args(["serve"]), Config.load(None))
    assert captured["config_path"] == str(toml)
    assert captured["db_path"] == (tmp_path / "x.db").as_posix()


def test_load_config_respects_env_config(tmp_path, monkeypatch):
    """Все CLI-команды должны уважать V8HELP_CONFIG (контейнер: /app/v8help.toml)."""
    toml = tmp_path / "c.toml"
    toml.write_text(f'db_path = "{(tmp_path / "x.db").as_posix()}"\n')
    monkeypatch.setenv("V8HELP_CONFIG", str(toml))
    monkeypatch.delenv("V8HELP_DB_PATH", raising=False)
    cfg = _load_config(None)
    assert str(cfg.db_path).replace("\\", "/") == (tmp_path / "x.db").as_posix()

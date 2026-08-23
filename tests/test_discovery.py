from pathlib import Path

from v8help import config as config_mod
from v8help.config import (
    Config,
    _bin_dir_for,
    _fs_platform_bin_dirs,
    _is_1c_platform,
    _parse_dotted_version,
    _parse_version,
    discover_bin_dir,
    discover_platforms,
    reset_discovery_cache,
)


def test_parse_version():
    assert _parse_version("8.5.1.1423") == (8, 5, 1, 1423)
    assert _parse_version("8.3.27.2214") == (8, 3, 27, 2214)
    assert _parse_version("8.1.15") == (8, 1, 15)
    assert _parse_version("") is None
    assert _parse_version("abc") is None


def test_parse_dotted_version():
    assert _parse_dotted_version("1C:Enterprise 8 (x86-64) (8.5.1.1423)") == (8, 5, 1, 1423)
    assert _parse_dotted_version("1C:Enterprise 8.2 (8.2.19.130)") == (8, 2, 19, 130)
    assert _parse_dotted_version("no version here") is None


def test_is_1c_platform():
    assert _is_1c_platform("1С:Предприятие 8 (x86-64) (8.5.1.1423)")
    assert _is_1c_platform("1C:Enterprise 8 (x86-64) (8.5.1.1423)")
    assert _is_1c_platform("1C:Enterprise 8.3")
    assert not _is_1c_platform("PostgreSQL 17.5-1.1C(x64)")
    assert not _is_1c_platform("PostgreSQL for 1C 17 (64bit)")
    assert not _is_1c_platform("")


def test_bin_dir_for_loc_and_bin(tmp_path):
    loc = tmp_path / "8.5.1.1423"
    (loc / "bin").mkdir(parents=True)
    (loc / "bin" / "shcntx_ru.hbk").write_bytes(b"x")
    assert _bin_dir_for(loc) == loc / "bin"


def test_fs_platform_bin_dirs(tmp_path, monkeypatch):
    base = tmp_path / "1cv8"
    (base / "8.5.1.1423").mkdir(parents=True)
    (base / "8.3.27.2214").mkdir(parents=True)
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path))
    monkeypatch.delenv("PROGRAMFILES(X86)", raising=False)
    versions = {v for v, _ in _fs_platform_bin_dirs()}
    assert (8, 5, 1, 1423) in versions
    assert (8, 3, 27, 2214) in versions


def test_discover_picks_highest_with_hbk(tmp_path, monkeypatch):
    base = tmp_path / "1cv8"
    for ver in ("8.3.27.2214", "8.5.1.1423"):
        (base / ver / "bin").mkdir(parents=True)
        (base / ver / "bin" / "shcntx_ru.hbk").write_bytes(b"x")
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path))
    monkeypatch.delenv("PROGRAMFILES(X86)", raising=False)
    monkeypatch.setattr(config_mod, "_registry_1c_installs", lambda: [])
    reset_discovery_cache()

    bd = discover_bin_dir()
    assert bd is not None
    assert bd.name == "bin"
    assert bd.parent.name == "8.5.1.1423"

    plats = discover_platforms()
    assert plats[0]["version"] == "8.5.1.1423"


def test_resolve_sources_uses_discovered_bin_dir(tmp_path, monkeypatch):
    base = tmp_path / "1cv8"
    (base / "8.5.1.1423" / "bin").mkdir(parents=True)
    for b in ("shcntx_ru", "shlang_ru", "shquery_ru", "shclang_ru"):
        (base / "8.5.1.1423" / "bin" / f"{b}.hbk").write_bytes(b"x")
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path))
    monkeypatch.delenv("PROGRAMFILES(X86)", raising=False)
    monkeypatch.setattr(config_mod, "_registry_1c_installs", lambda: [])
    reset_discovery_cache()

    cfg = Config()
    sources = cfg.resolve_sources()
    assert len(sources) == 4
    assert all(s.hbk.exists() for s in sources)


def test_resolve_sources_empty_books_no_discovery(monkeypatch):
    monkeypatch.setattr(config_mod, "_registry_1c_installs", lambda: [])
    monkeypatch.delenv("PROGRAMFILES", raising=False)
    monkeypatch.delenv("PROGRAMFILES(X86)", raising=False)
    reset_discovery_cache()
    cfg = Config()
    cfg.books = []
    assert cfg.resolve_sources() == []

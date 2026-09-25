"""Конфигурация приложения (TOML)."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BOOKS = ["shcntx_ru", "shlang_ru", "shquery_ru", "shclang_ru", "1cv8_ru"]

# Базовые книги -> (префикс выходных имён, v8help-неймспейс).
BOOK_META = {
    "shcntx": ("", "SyntaxHelperContext"),
    "shlang": ("lang__", "SyntaxHelperLanguage"),
    "shquery": ("query__", "SyntaxHelperQueries"),
    "shclang": ("clang__", "SyntaxHelperCommonLanguage"),
    "dcsui": ("dcsui__", "dcsui"),
    "1cv8": ("1cv8__", "1cv8"),
}


@dataclass
class EmbedderConfig:
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    dims: int = 0
    batch_size: int = 64
    embed_chars: int = 500
    threads: int = 2
    provider: str = ""  # "openai" (по умолчанию) | "hf" (Hugging Face native pipeline)


@dataclass
class SearchConfig:
    backend: str = "fts"
    limit: int = 10
    max_chunks_per_page: int = 2


@dataclass
class BuildConfig:
    cleanup: bool = False
    chunk_size: int = 1500
    chunk_overlap: int = 200


@dataclass
class SourceSpec:
    """Одна книга справки: .hbk + выходной префикс + неймспейс ссылок."""

    id: str
    hbk: Path
    prefix: str = ""
    scheme: str = ""
    lang: str = "ru"


@dataclass
class Config:
    corpus_dir: Path = Path("data/corpus")
    db_path: Path = Path("data/v8help.db")
    bin_dir: Path = Path("")
    books: list[str] = field(default_factory=lambda: list(DEFAULT_BOOKS))
    sources: list[SourceSpec] = field(default_factory=list)
    lang: str = "ru"
    include_english: bool = False
    edt_cli: Path = Path("")
    edt_docs: bool = True
    embedder_index: EmbedderConfig = field(default_factory=EmbedderConfig)
    embedder_query: EmbedderConfig = field(default_factory=EmbedderConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    build: BuildConfig = field(default_factory=BuildConfig)

    @classmethod
    def load(cls, path: str | Path | None = None) -> Config:
        if path is None:
            cfg = cls()
        else:
            with open(path, "rb") as fh:
                data = tomllib.load(fh)
            cfg = cls.from_dict(data)
        _apply_env_overrides(cfg)
        return cfg

    @classmethod
    def from_dict(cls, data: dict) -> Config:
        cfg = cls()
        _apply_basic(cfg, data)
        cfg.sources = _sources_from(data, cfg.lang)
        embedder = data.get("embedder") or {}
        cfg.embedder_index = _embedder(embedder.get("index") or {})
        cfg.embedder_query = _embedder(embedder.get("query") or {})
        if "search" in data:
            cfg.search = _search_from(data["search"], cfg.search)
        cfg.build = _build_from(data, cfg.build)
        return cfg

    def resolve_bin_dir(self) -> Path:
        """Каталог bin платформы: явный ``bin_dir`` либо автодискавери (реестр/ФС)."""
        if str(self.bin_dir) not in ("", "."):
            return self.bin_dir
        return discover_bin_dir() or Path("")

    def resolve_edt_cli(self) -> Path | None:
        """CLI 1C:EDT: явный ``edt_cli`` либо автодискавери; None, если не найден."""
        if str(self.edt_cli) not in ("", "."):
            p = Path(self.edt_cli)
            return p if p.is_file() else None
        return discover_edt_cli()

    def resolve_sources(self, lang: str | None = None) -> list[SourceSpec]:
        """Источники для сборки: явный [[sources]] либо books+bin_dir (shorthand)."""
        lang = lang or self.lang
        if self.sources:
            return [s for s in self.sources if s.lang == lang]
        if not self.books:
            return []
        bin_dir = self.resolve_bin_dir()
        if str(bin_dir) in ("", "."):
            raise RuntimeError(SOURCES_NO_BINDIR)
        return [_book_source(b, bin_dir, lang) for b in self.books]

    def to_dict(self) -> dict:
        """Сериализация для config_get / персиста в TOML."""
        d: dict = {
            "bin_dir": str(self.bin_dir) if str(self.bin_dir) not in ("", ".") else "",
            "corpus_dir": str(self.corpus_dir),
            "db_path": str(self.db_path),
            "lang": self.lang,
            "books": list(self.books),
            "include_english": self.include_english,
            "edt_cli": str(self.edt_cli) if str(self.edt_cli) not in ("", ".") else "",
            "edt_docs": self.edt_docs,
            "search": _search_dict(self.search),
            "build": _build_dict(self.build),
            "embedder": {
                "index": _emb_dict(self.embedder_index),
                "query": _emb_dict(self.embedder_query),
            },
        }
        if self.sources:
            d["sources"] = [_source_dict(s) for s in self.sources]
        return d


def _apply_basic(cfg: Config, data: dict) -> None:
    """Простые скалярные/списковые поля конфига (без вложенных секций)."""
    if "corpus_dir" in data:
        cfg.corpus_dir = Path(data["corpus_dir"])
    if "db_path" in data:
        cfg.db_path = Path(data["db_path"])
    if "bin_dir" in data:
        cfg.bin_dir = Path(data["bin_dir"])
    if "books" in data:
        cfg.books = list(data["books"])
    if "lang" in data:
        cfg.lang = str(data["lang"])
    if "include_english" in data:
        cfg.include_english = bool(data["include_english"])
    if "edt_cli" in data:
        cfg.edt_cli = Path(str(data["edt_cli"]))
    if "edt_docs" in data:
        cfg.edt_docs = bool(data["edt_docs"])


def _embedder(data: dict) -> EmbedderConfig:
    return EmbedderConfig(
        model=data.get("model", ""),
        base_url=data.get("base_url", ""),
        api_key=data.get("api_key", ""),
        dims=int(data.get("dims", 0)),
        batch_size=int(data.get("batch_size", 64)),
        embed_chars=int(data.get("embed_chars", 500)),
        threads=int(data.get("threads", 2)),
        provider=str(data.get("provider", "")),
    )


def _emb_dict(e: EmbedderConfig) -> dict:
    return {
        "model": e.model, "base_url": e.base_url, "api_key": e.api_key,
        "dims": e.dims, "batch_size": e.batch_size,
        "embed_chars": e.embed_chars, "threads": e.threads, "provider": e.provider,
    }


def _source_dict(s: SourceSpec) -> dict:
    return {
        "id": s.id, "hbk": str(s.hbk), "prefix": s.prefix,
        "scheme": s.scheme, "lang": s.lang,
    }


SOURCES_NO_BINDIR = (
    "bin_dir не задан и не найден автоматически. Укажите bin_dir в "
    "конфиге или проверьте установку платформы 1С (реестр Uninstall)."
)


def _book_source(book: str, bin_dir: Path, lang: str) -> SourceSpec:
    prefix, scheme = _book_meta(book)
    return SourceSpec(
        id=book,
        hbk=bin_dir / f"{book}.hbk",
        prefix=prefix,
        scheme=scheme,
        lang=_book_lang(book, lang),
    )


def _sources_from(data: dict, default_lang: str) -> list[SourceSpec]:
    return [
        SourceSpec(
            id=str(s.get("id", "")),
            hbk=Path(s.get("hbk", "")),
            prefix=str(s.get("prefix", "")),
            scheme=str(s.get("scheme", "")),
            lang=str(s.get("lang", default_lang)),
        )
        for s in data.get("sources", [])
    ]


def _search_from(s: dict, default: SearchConfig) -> SearchConfig:
    return SearchConfig(
        backend=s.get("backend", default.backend),
        limit=int(s.get("limit", default.limit)),
        max_chunks_per_page=int(
            s.get("max_chunks_per_page", default.max_chunks_per_page)
        ),
    )


def _search_dict(s: SearchConfig) -> dict:
    return {
        "backend": s.backend, "limit": s.limit,
        "max_chunks_per_page": s.max_chunks_per_page,
    }


def _build_from(data: dict, default: BuildConfig) -> BuildConfig:
    build = data.get("build") or {}
    return BuildConfig(
        cleanup=bool(build.get("cleanup", default.cleanup)),
        chunk_size=int(build.get("chunk_size", default.chunk_size)),
        chunk_overlap=int(build.get("chunk_overlap", default.chunk_overlap)),
    )


def _build_dict(b: BuildConfig) -> dict:
    return {
        "cleanup": b.cleanup, "chunk_size": b.chunk_size,
        "chunk_overlap": b.chunk_overlap,
    }


def _book_meta(book: str) -> tuple[str, str]:
    base = book
    for suffix in ("_ru", "_root", "_en"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return BOOK_META.get(base, ("", ""))


def _book_lang(book: str, default: str) -> str:
    if book.endswith("_ru"):
        return "ru"
    if book.endswith(("_root", "_en")):
        return "en"
    return default


# ---------- Приоритет env V8HELP_* над файлом конфига (12-factor) ------------

_ENV_BOOL_TRUE = {"1", "true", "yes", "on"}

# env → (плоский ключ в Config, тип значения).
_ENV_SCALARS = {
    "V8HELP_DB_PATH": ("db_path", "path"),
    "V8HELP_CORPUS_DIR": ("corpus_dir", "path"),
    "V8HELP_BIN_DIR": ("bin_dir", "path"),
    "V8HELP_LANG": ("lang", "str"),
    "V8HELP_BOOKS": ("books", "books"),
    "V8HELP_INCLUDE_ENGLISH": ("include_english", "bool"),
    "V8HELP_EDT_CLI": ("edt_cli", "path"),
    "V8HELP_EDT_DOCS": ("edt_docs", "bool"),
    "V8HELP_BACKEND": ("search.backend", "str"),
    "V8HELP_LIMIT": ("search.limit", "int"),
    "V8HELP_MAX_CHUNKS_PER_PAGE": ("search.max_chunks_per_page", "int"),
    "V8HELP_BUILD_CLEANUP": ("build.cleanup", "bool"),
    "V8HELP_BUILD_CHUNK_SIZE": ("build.chunk_size", "int"),
    "V8HELP_BUILD_CHUNK_OVERLAP": ("build.chunk_overlap", "int"),
}

_EMBEDDER_ENV_FIELDS = (
    "model", "base_url", "api_key", "provider",
    "dims", "batch_size", "embed_chars", "threads",
)


def _cvt_env(kind: str, value: str):
    if kind == "path":
        return Path(value)
    if kind == "bool":
        return str(value).strip().lower() in _ENV_BOOL_TRUE
    if kind == "int":
        return int(value)
    if kind == "books":
        return [b.strip() for b in value.split(",") if b.strip()]
    return str(value)


def _set_dotted(cfg: Config, key: str, value) -> None:
    """Присваивание по плоскому ключу 'a.b' (b — атрибут cfg.a) или 'a'."""
    obj = cfg
    parts = key.split(".")
    for p in parts[:-1]:
        obj = getattr(obj, p)
    setattr(obj, parts[-1], value)


def _apply_embedder_env(target, prefix: str) -> None:
    """V8HELP_EMBEDDER_{INDEX,QUERY}_<FIELD> поверх одного EmbedderConfig."""
    ints = ("dims", "batch_size", "embed_chars", "threads")
    for field in _EMBEDDER_ENV_FIELDS:
        raw = os.environ.get(prefix + field.upper())
        if raw is None or raw == "":
            continue
        try:
            value = int(raw) if field in ints else str(raw)
        except ValueError:
            continue
        setattr(target, field, value)


def _apply_env_overrides(cfg: Config) -> None:
    """Env V8HELP_* перекрывают значения из TOML (для контейнера/Docker)."""
    for env, (key, kind) in _ENV_SCALARS.items():
        raw = os.environ.get(env)
        if raw is None or raw == "":
            continue
        try:
            value = _cvt_env(kind, raw)
        except ValueError:
            continue
        _set_dotted(cfg, key, value)
    _apply_embedder_env(cfg.embedder_index, "V8HELP_EMBEDDER_INDEX_")
    _apply_embedder_env(cfg.embedder_query, "V8HELP_EMBEDDER_QUERY_")


# ---------- Автодискавери каталога bin платформы 1С ------------------------

_VERSION_RE = re.compile(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:\.(\d+))?")


def _parse_version(s: str) -> tuple[int, ...] | None:
    """'8.5.1.1423' -> (8, 5, 1, 1423); None если версию не выделить."""
    if not s:
        return None
    m = _VERSION_RE.search(s)
    if not m:
        return None
    parts = tuple(int(x) for x in m.groups() if x is not None)
    return parts or None


_VERSION_DOTTED_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def _parse_dotted_version(s: str) -> tuple[int, ...] | None:
    """Версия x.y[.z[.w]] из произвольной строки (не цепляет «1» в «1C»).

    Берёт самое длинное точечное число (например из «… 8.2 (8.2.19.130)» → 8.2.19.130).
    """
    if not s:
        return None
    best: tuple[int, ...] | None = None
    for m in _VERSION_DOTTED_RE.finditer(s):
        parts = tuple(int(x) for x in m.groups() if x is not None)
        if parts and (best is None or len(parts) > len(best)):
            best = parts
    return best


def _is_1c_platform(display_name: str) -> bool:
    """«1С:Предприятие» или английская локализация «1C:Enterprise 8»."""
    if not display_name:
        return False
    low = display_name.casefold()
    if "предприятие" in low:
        return True
    return "enterprise" in low and ("1c" in low or "1с" in low)


def _reg_str(key, name: str) -> str:
    try:
        import winreg

        val, _ = winreg.QueryValueEx(key, name)
        return str(val) if val else ""
    except OSError:
        return ""


_UNINSTALL_TAIL = r"Microsoft\Windows\CurrentVersion\Uninstall"
_UNINSTALL_SUB = "SOFTWARE\\" + _UNINSTALL_TAIL
_UNINSTALL_WOW_SUB = "SOFTWARE\\WOW6432Node\\" + _UNINSTALL_TAIL


def _uninstall_entries(winreg, hive, subkey: str) -> list[tuple[str, str, str]]:
    """(DisplayName, DisplayVersion, InstallLocation) продуктов из ключа Uninstall."""
    try:
        key = winreg.OpenKey(hive, subkey)
    except OSError:
        return []
    out: list[tuple[str, str, str]] = []
    with key:
        i = 0
        while True:
            try:
                name = winreg.EnumKey(key, i)
            except OSError:
                break
            i += 1
            try:
                with winreg.OpenKey(key, name) as sk:
                    out.append((
                        _reg_str(sk, "DisplayName"),
                        _reg_str(sk, "DisplayVersion"),
                        _reg_str(sk, "InstallLocation"),
                    ))
            except OSError:
                continue
    return out


def _registry_1c_installs() -> list[tuple[tuple[int, ...], Path]]:
    """Установки «1С:Предприятие» из реестра Uninstall (HKLM/WOW6432Node/HKCU)."""
    try:
        import winreg
    except ImportError:
        return []
    roots = (
        (winreg.HKEY_LOCAL_MACHINE, _UNINSTALL_SUB),
        (winreg.HKEY_LOCAL_MACHINE, _UNINSTALL_WOW_SUB),
        (winreg.HKEY_CURRENT_USER, _UNINSTALL_SUB),
    )
    out: list[tuple[tuple[int, ...], Path]] = []
    for hive, sub in roots:
        for dn, ver, loc in _uninstall_entries(winreg, hive, sub):
            if not _is_1c_platform(dn) or not loc:
                continue
            v = _parse_version(ver) or _parse_dotted_version(dn)
            if v is not None:
                out.append((v, Path(loc)))
    return out


def _fs_platform_bin_dirs() -> list[tuple[tuple[int, ...], Path]]:
    """Fallback: сканируем %ProgramFiles%\\1cv8\\<version> на случай портативной установки."""
    out: list[tuple[tuple[int, ...], Path]] = []
    for env in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
        root = os.environ.get(env)
        if not root:
            continue
        base = Path(root) / "1cv8"
        if not base.is_dir():
            continue
        for child in base.iterdir():
            if not child.is_dir():
                continue
            v = _parse_version(child.name)
            if v is None:
                continue
            out.append((v, child))
    return out


def _bin_dir_for(loc: Path) -> Path | None:
    """InstallLocation может указывать на корень версии (…\\8.5.1.1423\\) или сразу на bin."""
    for cand in (loc / "bin", loc):
        try:
            if cand.is_dir() and (any(cand.glob("*.hbk")) or _has_1cv8_binary(cand)):
                return cand
        except OSError:
            continue
    return None


def _has_1cv8_binary(d: Path) -> bool:
    """В каталоге есть исполняемый файл платформы (1cv8 / 1cv8.exe / 1cv8c…)."""
    try:
        for name in ("1cv8", "1cv8.exe", "1cv8c", "1cv8c.exe"):
            if (d / name).exists():
                return True
    except OSError:
        pass
    return False


_LINUX_1CV8_ROOTS = ("/opt", "/usr/lib", "/usr/local")
_LINUX_MAX_DEPTH = 6


def _version_from_path(path: Path) -> tuple[int, ...] | None:
    """Первая компонента пути (снизу вверх), похожая на версию платформы."""
    for p in (path, *path.parents):
        v = _parse_version(p.name)
        if v and len(v) >= 2:
            return v
    return None


def _walk_1cv8(base: Path, seen: set[Path]) -> list[tuple[tuple[int, ...], Path]]:
    """Каталоги вида <root>/1cv8/**/<version>[/bin] с .hbk/бинарями платформы."""
    out: list[tuple[tuple[int, ...], Path]] = []
    for dirpath, dirnames, _files in os.walk(base):
        cur = Path(dirpath)
        if len(cur.relative_to(base).parts) >= _LINUX_MAX_DEPTH:
            dirnames[:] = []
            continue
        v = _version_from_path(cur)
        if not v or cur in seen:
            continue
        if _bin_dir_for(cur) is not None:
            seen.add(cur)
            out.append((v, cur))
    return out


def _which_1cv8(seen: set[Path]) -> tuple[tuple[int, ...], Path] | None:
    """Каталог версии возле исполняемого файла, найденного ``which 1cv8``."""
    exe = shutil.which("1cv8")
    if not exe:
        return None
    bd = Path(os.path.realpath(exe)).parent
    cand = bd.parent if bd.name == "bin" else bd
    v = _version_from_path(bd)
    if v and cand not in seen and (_has_1cv8_binary(bd) or any(bd.glob("*.hbk"))):
        return (v, cand)
    return None


def _linux_platform_dirs() -> list[tuple[tuple[int, ...], Path]]:
    """Установки 1С под Linux: <root>/1cv8/**/<version>[/bin] + which 1cv8.

    Раскладки: классическая /opt/1cv8/x86_64/<version>/bin и 8.5.x, где
    бинарники и .hbk лежат прямо в каталоге версии (без bin).
    """
    out: list[tuple[tuple[int, ...], Path]] = []
    seen: set[Path] = set()
    for root in _LINUX_1CV8_ROOTS:
        base = Path(root) / "1cv8"
        if base.is_dir():
            out += _walk_1cv8(base, seen)
    entry = _which_1cv8(seen)
    if entry:
        out.append(entry)
    return out


def _is_darwin() -> bool:
    """Признак macOS; монкипатчится в тестах, чтобы гонять darwin-ветку на любом раннере."""
    return sys.platform == "darwin"


# Основной /opt/1cv8/<version> покрывает общий posix-скан (_LINUX_1CV8_ROOTS
# включает /opt); здесь только доп.(macOS-специфичные базы ручной установки).
_MACOS_1CV8_BASES = ("/usr/local/opt/1cv8",)


def _macos_platform_dirs() -> list[tuple[tuple[int, ...], Path]]:
    """macOS (8.3.x pkg и 8.5.x dmg): версии в /opt/1cv8/<version> с бинарями
    прямо в каталоге версии; фолбэк ручной установки — /usr/local/opt/1cv8.
    Обёртки 1Cv8*.app в /Applications .hbk не содержат и не сканируются.
    """
    out: list[tuple[tuple[int, ...], Path]] = []
    seen: set[Path] = set()
    for base_s in _MACOS_1CV8_BASES:
        base = Path(base_s)
        if base.is_dir():
            out += _walk_1cv8(base, seen)
    return out


def _collect_platforms() -> list[tuple[tuple[int, ...], Path]]:
    """Все bin-каталоги платформы с .hbk, по убыванию версии."""
    candidates = _registry_1c_installs()
    fs = _fs_platform_bin_dirs()
    if os.name == "posix":
        fs += _linux_platform_dirs()
    if _is_darwin():
        fs += _macos_platform_dirs()
    seen = {p for _, p in candidates}
    for v, p in fs:
        if p not in seen:
            candidates.append((v, p))
    resolved: list[tuple[tuple[int, ...], Path]] = []
    seen_bin: set[Path] = set()
    for v, loc in sorted(candidates, key=lambda x: x[0], reverse=True):
        bd = _bin_dir_for(loc)
        if bd is None or bd in seen_bin:
            continue
        seen_bin.add(bd)
        resolved.append((v, bd))
    return resolved


def discover_platforms() -> list[dict]:
    """Все найденные платформы: [{version, bin_dir}], по убыванию версии."""
    return [
        {"version": ".".join(map(str, v)), "bin_dir": str(bd)}
        for v, bd in _collect_platforms()
    ]


_bin_dir_cache: tuple[bool, Path | None] = (False, None)


def discover_bin_dir() -> Path | None:
    """Каталог bin самой свежей установленной платформы 1С (или None)."""
    global _bin_dir_cache
    done, val = _bin_dir_cache
    if done:
        return val
    plats = _collect_platforms()
    val = plats[0][1] if plats else None
    _bin_dir_cache = (True, val)
    return val


def reset_discovery_cache() -> None:
    global _bin_dir_cache, _embedders_cache, _edt_cli_cache
    _bin_dir_cache = (False, None)
    _embedders_cache = (False, [])
    _edt_cli_cache = (False, None)


# ---------- Дискавери эмбеддеров (OpenAI-совместимые /v1/models) -------------

_EMBEDDER_PORTS = (1234, 11434, 8000, 8080, 4891, 5000, 3000)

_embedders_cache: tuple[bool, list[dict]] = (False, [])


def _probe_models(base: str, timeout: float) -> list[str]:
    """Идентификаторы моделей из OpenAI-совместимого ``{base}/models`` ([] если недоступен)."""
    import urllib.request as _ur
    try:
        req = _ur.Request(f"{base}/models", method="GET")
        with _ur.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []
    items = data.get("data") if isinstance(data, dict) else None
    return [
        m.get("id", "")
        for m in (items or [])
        if isinstance(m, dict) and m.get("id")
    ]


def discover_embedders(timeout: float = 0.8) -> list[dict]:
    """Пробует OpenAI-совместимые ``/v1/models`` на localhost (LM Studio, Ollama, …).

    Возвращает ``[{base_url, models, embedding_models}]``. Результат кешируется.
    """
    global _embedders_cache
    done, val = _embedders_cache
    if done:
        return val
    out: list[dict] = []
    for port in _EMBEDDER_PORTS:
        base = f"http://localhost:{port}/v1"
        models = _probe_models(base, timeout)
        if models:
            out.append(
                {
                    "base_url": base,
                    "models": models,
                    "embedding_models": [m for m in models if "embed" in m.lower()],
                }
            )
    _embedders_cache = (True, out)
    return out


# ---------- Автодискавери 1C:EDT CLI ----------------------------------------

_EDT_CLI_NAMES = ("1cedtcli.exe", "1cedtcli") if os.name == "nt" else ("1cedtcli",)

_edt_cli_cache: tuple[bool, Path | None] = (False, None)


def _edt_cli_in(loc: Path) -> Path | None:
    """1cedtcli в каталоге установки EDT (в корне или в подкаталоге ``1cedt``)."""
    for name in _EDT_CLI_NAMES:
        for cand in (loc / name, loc / "1cedt" / name):
            try:
                if cand.is_file():
                    return cand
            except OSError:
                continue
    return None


def _is_edt_name(display_name: str) -> bool:
    low = display_name.casefold()
    has_edt = "edt" in low or "development tools" in low
    return has_edt and ("1c" in low or "1с" in low)


def _edt_registry_dirs() -> list[Path]:
    """Каталоги установок EDT из реестра Uninstall (HKLM/WOW6432Node/HKCU)."""
    try:
        import winreg
    except ImportError:
        return []
    roots = (
        (winreg.HKEY_LOCAL_MACHINE, _UNINSTALL_SUB),
        (winreg.HKEY_LOCAL_MACHINE, _UNINSTALL_WOW_SUB),
        (winreg.HKEY_CURRENT_USER, _UNINSTALL_SUB),
    )
    out: list[Path] = []
    for hive, sub in roots:
        for dn, _ver, loc in _uninstall_entries(winreg, hive, sub):
            if loc and _is_edt_name(dn):
                out.append(Path(loc))
    return out


def _edt_fs_dirs() -> list[Path]:
    """Известные раскладки установок EDT (компоненты 1C:EDT / 1cedtstart)."""
    roots: list[Path] = []
    prog = os.environ.get("PROGRAMFILES")
    if prog:
        roots.append(Path(prog) / "1C" / "1CE" / "components")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "1C" / "1cedtstart" / "installations")
    roots += [Path("/Applications/1C/1CE/components"), Path("/opt/1C/1CE/components")]
    out: list[Path] = []
    for root in roots:
        try:
            if not root.is_dir():
                continue
            out += [c for c in root.iterdir() if c.is_dir()]
        except OSError:
            continue
    return out


def discover_edt(force: bool = False) -> dict:
    """Найденная установка 1C:EDT CLI: ``{found, cli, version}`` (кешируется)."""
    cli = discover_edt_cli(force=force)
    if cli is None:
        return {"found": False, "cli": "", "version": ""}
    ver = _edt_version_of(cli)
    return {"found": True, "cli": str(cli), "version": ver}


def discover_edt_cli(force: bool = False) -> Path | None:
    """Путь к самому свежему ``1cedtcli`` или None. Результат кешируется."""
    global _edt_cli_cache
    done, val = _edt_cli_cache
    if done and not force:
        return val
    cands: list[tuple[tuple[int, ...], Path]] = []
    seen: set[Path] = set()
    for loc in _edt_registry_dirs() + _edt_fs_dirs():
        cli = _edt_cli_in(loc)
        if cli is None or cli in seen:
            continue
        seen.add(cli)
        ver = _parse_dotted_version(loc.name) or (0,)
        cands.append((ver, cli))
    val = max(cands, key=lambda x: x[0])[1] if cands else None
    _edt_cli_cache = (True, val)
    return val


def _edt_version_of(cli: Path) -> str:
    """Версия EDT из имени каталога установки (для мета и пересборки)."""
    for p in (cli.parent, cli.parent.parent):
        v = _parse_dotted_version(p.name)
        if v:
            return ".".join(map(str, v))
    return ""


# ---------- Минимальный TOML-сериализатор (для config_set) -------------------

def _toml_scalar(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    raise TypeError(f"Неподдерживаемый тип для TOML: {type(v)}")


def _toml_value(k: str, v) -> str:
    if isinstance(v, list):
        return f"{k} = [{', '.join(_toml_scalar(x) for x in v)}]"
    return f"{k} = {_toml_scalar(v)}"


def _emit_table(tables: list[str], name: str, table: dict) -> None:
    """Один [name]-блок: скаляры, массивы и один уровень вложенных подтаблиц."""
    tables.append(f"[{name}]")
    for k, v in table.items():
        if isinstance(v, dict):
            tables.append(f"[{name}.{k}]")
            for kk, vv in v.items():
                tables.append(f"{kk} = {_toml_scalar(vv)}")
        else:
            tables.append(_toml_value(k, v))


def config_to_toml(data: dict) -> str:
    """Сериализует структуру ``Config.to_dict()`` в валидный TOML."""
    scalars: list[str] = []
    tables: list[str] = []
    for k, v in data.items():
        if isinstance(v, dict):
            _emit_table(tables, k, v)
        elif isinstance(v, list) and v and isinstance(v[0], dict):
            tables += _emit_array_of_tables(k, v)
        elif isinstance(v, list):
            scalars.append(_toml_value(k, v))
        else:
            scalars.append(_toml_value(k, v))
    body = "\n".join(scalars + tables)
    return (body + "\n") if body else ""


def _emit_array_of_tables(name: str, items: list[dict]) -> list[str]:
    """Блоки [[name]] для списков словарей (sources)."""
    out: list[str] = []
    for item in items:
        out.append(f"[[{name}]]")
        out += [f"{kk} = {_toml_scalar(vv)}" for kk, vv in item.items()]
    return out

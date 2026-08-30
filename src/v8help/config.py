"""Конфигурация приложения (TOML)."""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BOOKS = ["shcntx_ru", "shlang_ru", "shquery_ru", "shclang_ru"]

# Базовые книги -> (префикс выходных имён, v8help-неймспейс).
BOOK_META = {
    "shcntx": ("", "SyntaxHelperContext"),
    "shlang": ("lang__", "SyntaxHelperLanguage"),
    "shquery": ("query__", "SyntaxHelperQueries"),
    "shclang": ("clang__", "SyntaxHelperCommonLanguage"),
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
    embedder_index: EmbedderConfig = field(default_factory=EmbedderConfig)
    embedder_query: EmbedderConfig = field(default_factory=EmbedderConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    build: BuildConfig = field(default_factory=BuildConfig)

    @classmethod
    def load(cls, path: str | Path | None = None) -> Config:
        if path is None:
            return cls()
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> Config:
        cfg = cls()
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

        cfg.sources = [
            SourceSpec(
                id=str(s.get("id", "")),
                hbk=Path(s.get("hbk", "")),
                prefix=str(s.get("prefix", "")),
                scheme=str(s.get("scheme", "")),
                lang=str(s.get("lang", cfg.lang)),
            )
            for s in data.get("sources", [])
        ]

        embedder = data.get("embedder") or {}
        cfg.embedder_index = _embedder(embedder.get("index") or {})
        cfg.embedder_query = _embedder(embedder.get("query") or {})

        if "search" in data:
            s = data["search"]
            cfg.search = SearchConfig(
                backend=s.get("backend", cfg.search.backend),
                limit=int(s.get("limit", cfg.search.limit)),
                max_chunks_per_page=int(
                    s.get("max_chunks_per_page", cfg.search.max_chunks_per_page)
                ),
            )

        build = data.get("build") or {}
        cfg.build = BuildConfig(
            cleanup=bool(build.get("cleanup", cfg.build.cleanup)),
            chunk_size=int(build.get("chunk_size", cfg.build.chunk_size)),
            chunk_overlap=int(build.get("chunk_overlap", cfg.build.chunk_overlap)),
        )
        return cfg

    def resolve_bin_dir(self) -> Path:
        """Каталог bin платформы: явный ``bin_dir`` либо автодискавери (реестр/ФС)."""
        if str(self.bin_dir) not in ("", "."):
            return self.bin_dir
        return discover_bin_dir() or Path("")

    def resolve_sources(self, lang: str | None = None) -> list[SourceSpec]:
        """Источники для сборки: явный [[sources]] либо books+bin_dir (shorthand)."""
        lang = lang or self.lang
        if self.sources:
            return [s for s in self.sources if s.lang == lang]
        if not self.books:
            return []
        bin_dir = self.resolve_bin_dir()
        if str(bin_dir) in ("", "."):
            raise RuntimeError(
                "bin_dir не задан и не найден автоматически. Укажите bin_dir в "
                "конфиге или проверьте установку платформы 1С (реестр Uninstall)."
            )
        out: list[SourceSpec] = []
        for book in self.books:
            prefix, scheme = _book_meta(book)
            out.append(
                SourceSpec(
                    id=book,
                    hbk=bin_dir / f"{book}.hbk",
                    prefix=prefix,
                    scheme=scheme,
                    lang=_book_lang(book, lang),
                )
            )
        return out

    def to_dict(self) -> dict:
        """Сериализация для config_get / персиста в TOML."""
        def _emb(e: EmbedderConfig) -> dict:
            return {
                "model": e.model,
                "base_url": e.base_url,
                "api_key": e.api_key,
                "dims": e.dims,
                "batch_size": e.batch_size,
                "embed_chars": e.embed_chars,
                "threads": e.threads,
            }

        d: dict = {
            "bin_dir": str(self.bin_dir) if str(self.bin_dir) not in ("", ".") else "",
            "corpus_dir": str(self.corpus_dir),
            "db_path": str(self.db_path),
            "lang": self.lang,
            "books": list(self.books),
            "include_english": self.include_english,
            "search": {
                "backend": self.search.backend,
                "limit": self.search.limit,
                "max_chunks_per_page": self.search.max_chunks_per_page,
            },
            "build": {
                "cleanup": self.build.cleanup,
                "chunk_size": self.build.chunk_size,
                "chunk_overlap": self.build.chunk_overlap,
            },
            "embedder": {
                "index": _emb(self.embedder_index),
                "query": _emb(self.embedder_query),
            },
        }
        if self.sources:
            d["sources"] = [
                {
                    "id": s.id,
                    "hbk": str(s.hbk),
                    "prefix": s.prefix,
                    "scheme": s.scheme,
                    "lang": s.lang,
                }
                for s in self.sources
            ]
        return d


def _embedder(data: dict) -> EmbedderConfig:
    return EmbedderConfig(
        model=data.get("model", ""),
        base_url=data.get("base_url", ""),
        api_key=data.get("api_key", ""),
        dims=int(data.get("dims", 0)),
        batch_size=int(data.get("batch_size", 64)),
        embed_chars=int(data.get("embed_chars", 500)),
        threads=int(data.get("threads", 2)),
    )


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


def _registry_1c_installs() -> list[tuple[tuple[int, ...], Path]]:
    """Установки «1С:Предприятие» из реестра Uninstall (HKLM/WOW6432Node/HKCU)."""
    try:
        import winreg
    except ImportError:
        return []
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        ),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    out: list[tuple[tuple[int, ...], Path]] = []
    for hive, sub in roots:
        try:
            key = winreg.OpenKey(hive, sub)
        except OSError:
            continue
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
                        dn = _reg_str(sk, "DisplayName")
                        ver = _reg_str(sk, "DisplayVersion")
                        loc = _reg_str(sk, "InstallLocation")
                except OSError:
                    continue
                if not _is_1c_platform(dn):
                    continue
                v = _parse_version(ver) or _parse_dotted_version(dn)
                if v is None or not loc:
                    continue
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
            if cand.is_dir() and any(cand.glob("*.hbk")):
                return cand
        except OSError:
            continue
    return None


def _collect_platforms() -> list[tuple[tuple[int, ...], Path]]:
    """Все bin-каталоги платформы с .hbk, по убыванию версии."""
    candidates = _registry_1c_installs()
    fs = _fs_platform_bin_dirs()
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
    global _bin_dir_cache, _embedders_cache
    _bin_dir_cache = (False, None)
    _embedders_cache = (False, [])


# ---------- Дискавери эмбеддеров (OpenAI-совместимые /v1/models) -------------

_EMBEDDER_PORTS = (1234, 11434, 8000, 8080, 4891, 5000, 3000)

_embedders_cache: tuple[bool, list[dict]] = (False, [])


def discover_embedders(timeout: float = 0.8) -> list[dict]:
    """Пробует OpenAI-совместимые ``/v1/models`` на localhost (LM Studio, Ollama, …).

    Возвращает ``[{base_url, models, embedding_models}]``. Результат кешируется.
    """
    global _embedders_cache
    done, val = _embedders_cache
    if done:
        return val

    import urllib.request as _ur

    out: list[dict] = []
    for port in _EMBEDDER_PORTS:
        base = f"http://localhost:{port}/v1"
        try:
            req = _ur.Request(f"{base}/models", method="GET")
            with _ur.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            continue
        models = [m.get("id", "") for m in data.get("data", []) if m.get("id")]
        if not models:
            continue
        out.append(
            {
                "base_url": base,
                "models": models,
                "embedding_models": [m for m in models if "embed" in m.lower()],
            }
        )
    _embedders_cache = (True, out)
    return out


# ---------- Минимальный TOML-сериализатор (для config_set) -------------------

def _toml_scalar(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    raise TypeError(f"Неподдерживаемый тип для TOML: {type(v)}")


def config_to_toml(data: dict) -> str:
    """Сериализует структуру ``Config.to_dict()`` в валидный TOML."""
    scalars: list[str] = []
    tables: list[str] = []

    def _emit_table(name: str, table: dict) -> None:
        tables.append(f"[{name}]")
        for k, v in table.items():
            if isinstance(v, dict):
                tables.append(f"[{name}.{k}]")
                for kk, vv in v.items():
                    tables.append(f"{kk} = {_toml_scalar(vv)}")
            elif isinstance(v, list):
                tables.append(f"{k} = [{', '.join(_toml_scalar(x) for x in v)}]")
            else:
                tables.append(f"{k} = {_toml_scalar(v)}")

    for k, v in data.items():
        if isinstance(v, dict):
            _emit_table(k, v)
        elif isinstance(v, list) and v and isinstance(v[0], dict):
            for item in v:
                tables.append(f"[[{k}]]")
                for kk, vv in item.items():
                    tables.append(f"{kk} = {_toml_scalar(vv)}")
        elif isinstance(v, list):
            scalars.append(f"{k} = [{', '.join(_toml_scalar(x) for x in v)}]")
        else:
            scalars.append(f"{k} = {_toml_scalar(v)}")

    body = "\n".join(scalars + tables)
    return (body + "\n") if body else ""

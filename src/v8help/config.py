"""Конфигурация приложения (TOML)."""

from __future__ import annotations

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


@dataclass
class SearchConfig:
    backend: str = "fts"
    limit: int = 10


@dataclass
class BuildConfig:
    cleanup: bool = False


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
            )

        build = data.get("build") or {}
        cfg.build = BuildConfig(
            cleanup=bool(build.get("cleanup", cfg.build.cleanup)),
        )
        return cfg

    def resolve_sources(self, lang: str | None = None) -> list[SourceSpec]:
        """Источники для сборки: явный [[sources]] либо books+bin_dir (shorthand)."""
        lang = lang or self.lang
        if self.sources:
            return [s for s in self.sources if s.lang == lang]
        out: list[SourceSpec] = []
        for book in self.books:
            prefix, scheme = _book_meta(book)
            out.append(
                SourceSpec(
                    id=book,
                    hbk=self.bin_dir / f"{book}.hbk",
                    prefix=prefix,
                    scheme=scheme,
                    lang=_book_lang(book, lang),
                )
            )
        return out


def _embedder(data: dict) -> EmbedderConfig:
    return EmbedderConfig(
        model=data.get("model", ""),
        base_url=data.get("base_url", ""),
        api_key=data.get("api_key", ""),
        dims=int(data.get("dims", 0)),
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

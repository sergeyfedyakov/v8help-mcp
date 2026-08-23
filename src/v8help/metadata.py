"""Извлечение структурных метаданных страниц (title, section, kind, source, ссылки)."""

from __future__ import annotations

import re

_SECTION_BY_PREFIX = (
    ("lang__", "lang"),
    ("tables__", "tables"),
    ("objects__", "objects"),
    ("query__", "query"),
    ("clang__", "clang"),
)

_SCHEME_PREFIX = {
    "SyntaxHelperLanguage": "lang__",
    "SyntaxHelperQueries": "query__",
    "SyntaxHelperCommonLanguage": "clang__",
    "SyntaxHelperContext": "",
}

_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_H1_RE = re.compile(r"^#\s+(.+)$")


def detect_section(filename: str, default: str = "objects") -> str:
    for prefix, section in _SECTION_BY_PREFIX:
        if filename.startswith(prefix):
            return section
    return default


def detect_source(filename: str) -> str:
    if filename.startswith("lang__"):
        return "shlang_ru"
    if filename.startswith("query__"):
        return "shquery_ru"
    if filename.startswith("clang__"):
        return "shclang_ru"
    return "shcntx_ru"


def detect_kind(filename: str) -> str:
    stem = filename[:-3] if filename.endswith(".md") else filename
    if stem.startswith("_index"):
        return "index"
    if "." in stem:
        return "member"
    return "page"


def extract_title(text: str, filename: str) -> str:
    for line in text.splitlines():
        m = _H1_RE.match(line)
        if m:
            return m.group(1).strip()
    stem = filename[:-3] if filename.endswith(".md") else filename
    return stem


def _strip_ext(name: str) -> str:
    if name.endswith(".md"):
        name = name[:-3]
    elif name.endswith(".html"):
        name = name[:-5]
    return name


def normalize_target(target: str) -> str | None:
    if target.startswith("obsidian://"):
        return None
    if target.startswith(("http://", "https://")):
        return None
    if target.startswith("v8help://"):
        rest = target[len("v8help://"):]
        scheme, _, name = rest.partition("/")
        prefix = _SCHEME_PREFIX.get(scheme)
        if prefix is None:
            return None
        name = _strip_ext(name.split("#", 1)[0])
        return (prefix + name) if name else None
    name = _strip_ext(target.split("#", 1)[0])
    return name or None


def parse_links(body: str) -> list[str]:
    targets: list[str] = []
    for m in _LINK_RE.finditer(body):
        norm = normalize_target(m.group(1))
        if norm:
            targets.append(norm)
    return targets

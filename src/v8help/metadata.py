"""Извлечение структурных метаданных страниц (title, section, kind, source, ссылки)."""

from __future__ import annotations

import re

_SECTION_BY_PREFIX = (
    ("lang__", "lang"),
    ("tables__", "tables"),
    ("objects__", "objects"),
    ("query__", "query"),
    ("clang__", "clang"),
    ("dcsui__", "objects"),
)

_SCHEME_PREFIX = {
    "SyntaxHelperLanguage": "lang__",
    "SyntaxHelperQueries": "query__",
    "SyntaxHelperCommonLanguage": "clang__",
    "SyntaxHelperContext": "",
    "dcsui": "dcsui__",
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
    if filename.startswith("dcsui__"):
        return "dcsui_ru"
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


_DESC_LINE_RE = re.compile(
    r"^\s*\**\s*Описание(?:\s+варианта(?:\s+метода)?)?\s*:\s*\**\s*(.*)$",
    re.IGNORECASE,
)
_SECTION_RE = re.compile(r"^[^\s|#>\-*\d`]+[^:]{0,60}:\s*$")
_BOLD_SECTION_RE = re.compile(r"^\*\*[^*]+\*\*\s*$|^\*\*[^*]+:\*\*")


def _is_section_line(s: str) -> bool:
    """Строка-секция: 'Вариант синтаксиса: …', 'Синтаксис:', '**…:**'."""
    if s.startswith("Вариант синтаксиса"):
        return True
    return bool(_SECTION_RE.match(s) or _BOLD_SECTION_RE.match(s))


def _find_desc_heads(lines: list[str]) -> tuple[list[int], list[str]]:
    heads: list[int] = []
    tails: list[str] = []
    for i, line in enumerate(lines):
        m = _DESC_LINE_RE.match(line)
        if m:
            heads.append(i)
            tails.append(m.group(1).strip())
    return heads, tails


def _collect_desc_parts(
    lines: list[str], heads: list[int], tails: list[str]
) -> list[str]:
    parts: list[str] = []
    for k, desc_i in enumerate(heads):
        j = desc_i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if tails[k]:
            parts.append(tails[k])
        while j < len(lines):
            s = lines[j].strip()
            if s == "---" or _is_section_line(s):
                break
            if s:
                parts.append(s)
            j += 1
    return parts


def _flatten_desc(parts: list[str]) -> str:
    txt = " ".join(parts)
    txt = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", txt)
    txt = txt.replace("**", "")
    return re.sub(r"\s+", " ", txt).strip()


def extract_description(text: str) -> str:
    """Возвращает текст секций описания статьи (пустая строка, если секций нет).

    Собираются ВСЕ секции вида ``Описание:``, ``Описание варианта:``,
    ``Описание варианта метода:`` (статьи с несколькими вариантами синтаксиса) —
    каждая до ближайшей строки-секции (``…:``, ``Вариант синтаксиса: …``,
    ``**…:**``, ``---``). Markdown-ссылки сворачиваются в текст.
    """
    lines = text.split("\n")
    heads, tails = _find_desc_heads(lines)
    if not heads:
        return ""
    parts = _collect_desc_parts(lines, heads, tails)
    if not parts:
        return ""
    return _flatten_desc(parts)


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

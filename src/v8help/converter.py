"""Распаковка .hbk и конвертация HTML -> markdown.

Единый rich-конвертер для всех книг справки. Порт ядра ``third_party/hbk-to-md``
(без Obsidian-навигации: breadcrumbs / _index / signatures) с обобщёнными
v8help-неймспейсами и собственным ридером контейнера (см. ``v8help.unpack``).

Поведение имён:
- книги с ``V8SH_pagetitle`` (shcntx/shlang) — имена по заголовку;
- остальные (shquery/shclang/...) — имена по пути архива.
Ссылки ``v8help://...`` переписываются в относительные ``.md`` во всех случаях.
"""

from __future__ import annotations

import hashlib
import html as html_module
import posixpath
import re
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from bs4 import BeautifulSoup, NavigableString
from markdownify import markdownify as md_convert

from v8help.config import SourceSpec
from v8help.unpack.container import HbkContainer

MAX_FILENAME = 200

SCHEME_PREFIX = {
    "SyntaxHelperContext": "",
    "SyntaxHelperLanguage": "lang__",
    "SyntaxHelperQueries": "query__",
    "SyntaxHelperCommonLanguage": "clang__",
}

V8HELP_RE = re.compile(
    r"^v8help://(SyntaxHelperContext|SyntaxHelperLanguage|"
    r"SyntaxHelperQueries|SyntaxHelperCommonLanguage|dcsui)/(.+?)(?:#(.+))?$",
    re.IGNORECASE,
)
BROKEN_HREF_RE = re.compile(
    r'href\s*=\s*http[^\s"\'>]*\?[A-Za-z]+\s*=\s*"', re.IGNORECASE
)
AVAILABILITY_RE = re.compile(r"(8\.\d+(?:\.\d+)?)")
PAGETITLE_SPLIT_RE = re.compile(r"^(.*)\s*\(([^()]*)\)\s*$")

_H1_RE = re.compile(
    r'<h1[^>]*class=["\']V8SH_pagetitle["\'][^>]*>(.*?)</h1>',
    re.DOTALL | re.IGNORECASE,
)
_UNSAFE_CHARS = re.compile(r'[/\\:*?"<>|]')

PRIMITIVE_TYPE_STEMS: frozenset[str] = frozenset({
    "lang__def_String", "lang__def_Number", "lang__def_Date",
    "lang__def_Undefined", "lang__def_BooleanTrue", "lang__def_BooleanFalse", "lang__def_Null",
    "lang__Булево", "lang__Число", "lang__Строка",
    "lang__Дата", "lang__Неопределено",
})

CODE_FENCE_RE = re.compile(r"(```[\s\S]*?```)")
MD_LINK_TEXT_RE = re.compile(r"(?<!\\)\[((?:[^\[\]\\]|\\.)*?)\]\(")
METHODOLOGICAL_LINE_RE = re.compile(
    r'(?mi)^\s*(?:\[)?\s*Методическая информация(?:\]\([^)]*1centerprise\.com/devlinks[^)]*\))?\s*$'
)

_NON_HTML_EXT = {".st", ".data", ".png", ".jpg", ".jpeg", ".gif", ".css", ".js", ".bin"}
# ---------- Имена --------------------------------------------------------

def archive_path_to_filename(rel_path: str, prefix: str = "") -> str:
    rel = rel_path.replace("\\", "/").lstrip("./")
    if rel.lower().endswith(".html"):
        rel = rel[: -len(".html")] + ".md"
    elif rel.lower().endswith(".htm"):
        rel = rel[: -len(".htm")] + ".md"
    elif "." not in rel.rsplit("/", 1)[-1]:
        rel = rel + ".md"
    segments = [seg.replace(" ", "_") for seg in rel.split("/") if seg]
    name = "__".join(segments)
    if prefix and not name.startswith(prefix):
        name = prefix + name
    return name


def title_to_filename(title: str, prefix: str = "") -> str:
    name = _UNSAFE_CHARS.sub("", title).replace(" ", "_").strip("._")
    if not name:
        return ""
    stem = (prefix + name) if prefix else name
    return stem + ".md"


def truncate_filename(name: str, source_path: str) -> tuple[str, bool]:
    if len(name) <= MAX_FILENAME:
        return name, False
    digest = hashlib.sha1(source_path.encode("utf-8")).hexdigest()[:8]
    suffix = f"__TRUNC_{digest}.md"
    head_len = MAX_FILENAME - len(suffix)
    if head_len < 16:
        head_len = 16
    base = name[:-3] if name.endswith(".md") else name
    return base[:head_len] + suffix, True


def disambiguate(name: str, used: set[str]) -> tuple[str, bool]:
    if name not in used:
        return name, False
    if name.endswith(".md"):
        base, ext = name[:-3], ".md"
    else:
        base, ext = name, ""
    n = 2
    while True:
        candidate = f"{base}-{n}{ext}"
        if candidate not in used:
            return candidate, True
        n += 1


# ---------- Заголовки ----------------------------------------------------

def quick_extract_title(path: Path) -> str:
    raw = path.read_bytes()[:4096]
    text = raw.decode("utf-8-sig", errors="replace")
    m = _H1_RE.search(text)
    if not m:
        return ""
    return html_module.unescape(re.sub(r"<[^>]+>", "", m.group(1)).strip())


def quick_scan_titles(extracted_dir: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for html_path in iter_html(extracted_dir):
        rel = html_path.relative_to(extracted_dir).as_posix()
        result[rel.lower()] = quick_extract_title(html_path)
    return result


def resolve_parent_title(rel_path: str, title_map: dict[str, str]) -> str:
    parts = rel_path.split("/")
    for i in range(len(parts) - 2, 0, -1):
        candidate = "/".join(parts[:i]) + ".html"
        title = title_map.get(candidate.lower(), "")
        if title:
            return title
    return ""


# ---------- HTML ---------------------------------------------------------

def read_html(path: Path) -> str:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1251", errors="replace")


def parse_html(content: str) -> BeautifulSoup:
    cleaned = BROKEN_HREF_RE.sub("data-broken-href=\"", content)
    return BeautifulSoup(cleaned, "lxml")


def extract_titles(soup: BeautifulSoup) -> tuple[str, str]:
    h1 = soup.find("h1", class_="V8SH_pagetitle") or soup.find("h1")
    if not h1:
        return "", ""
    text = h1.get_text(strip=True)
    m = PAGETITLE_SPLIT_RE.match(text)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return text, ""


def extract_availability(soup: BeautifulSoup) -> str | None:
    p = soup.find("p", class_="V8SH_versionInfo")
    if not p:
        return None
    m = AVAILABILITY_RE.search(p.get_text(" ", strip=True))
    return m.group(1) if m else None


def _looks_like_html(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            head = f.read(64)
    except OSError:
        return False
    if head.startswith(b"\xef\xbb\xbf"):
        head = head[3:]
    head_lower = head.lower()
    return head_lower.startswith(b"<html") or head_lower.startswith(b"<!doctype")


def iter_html(extracted_dir: Path) -> Iterable[Path]:
    for p in extracted_dir.rglob("*"):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext in (".html", ".htm"):
            yield p
            continue
        if ext in _NON_HTML_EXT:
            continue
        if ext == "" and _looks_like_html(p):
            yield p


# ---------- Ссылки -------------------------------------------------------

def _normalize_archive_path(path: str) -> str:
    return "/".join(seg for seg in path.replace("\\", "/").split("/") if seg)


def _is_degenerate_v8help_target(normalized: str) -> bool:
    if not normalized:
        return True
    basename = normalized.rsplit("/", 1)[-1].lower()
    return basename in (".html", "html")


def _lookup_archive_target(
    rel_path: str,
    archive_index: dict[str, str],
    archive_lookup_final: dict[str, str],
) -> str | None:
    key = _normalize_archive_path(rel_path).lower()
    if not key:
        return None
    candidates = [key]
    if key.endswith(".html"):
        candidates.append(key[: -len(".html")])
    elif key.endswith(".htm"):
        candidates.append(key[: -len(".htm")])
    else:
        candidates.extend((key + ".html", key + ".htm"))
    for candidate in candidates:
        if candidate in archive_lookup_final:
            return archive_lookup_final[candidate]
    for candidate in candidates:
        if candidate in archive_index:
            return archive_index[candidate]
    return None


def _strip_link(a, unresolved_log: list[tuple[str, str]], page_source_path: str, href_s: str) -> None:
    unresolved_log.append((page_source_path, href_s))
    a.replace_with(NavigableString(a.get_text("", strip=False)))


_KEEP_LINK = object()


def _promote_v8help_scheme(href_s: str) -> str:
    if href_s.lower().startswith("v8help:") and not href_s.lower().startswith("v8help://"):
        return "v8help://" + href_s[7:].lstrip(":/")
    return href_s


def _v8help_link_target(
    m: re.Match,
    anchor: str | None,
    archive_index: dict[str, str],
    archive_lookup_final: dict[str, str],
) -> tuple[str | None, str | None]:
    anchor = m.group(3) or anchor
    normalized = _normalize_archive_path(m.group(2).lstrip("/"))
    if _is_degenerate_v8help_target(normalized):
        return None, anchor
    return (
        _lookup_archive_target(normalized, archive_index, archive_lookup_final),
        anchor,
    )


def _relative_link_target(
    path_part: str,
    page_source_path: str,
    archive_index: dict[str, str],
    archive_lookup_final: dict[str, str],
) -> str | object | None:
    path_part = path_part.split("?", 1)[0]
    if not path_part:
        return _KEEP_LINK
    base_dir = posixpath.dirname(page_source_path)
    resolved = _normalize_archive_path(
        posixpath.normpath(posixpath.join(base_dir, path_part)).lstrip("/")
    )
    return _lookup_archive_target(resolved, archive_index, archive_lookup_final)


def _is_external_href(href_s: str) -> bool:
    if href_s.lower().startswith(("http://", "https://", "mailto:", "ftp://")):
        return True
    return href_s.startswith("#")


def _link_target(
    href_raw: str,
    page_source_path: str,
    archive_index: dict[str, str],
    archive_lookup_final: dict[str, str],
) -> tuple[str | object | None, str | None, str]:
    """(таргет .md, anchor, канонический href) для одного <a>.

    Таргет: имя файла, ``_KEEP_LINK`` (ссылку не трогать) или ``None``
    (не разрезолвилась — ссылку снять с логированием).
    """
    href_s = _promote_v8help_scheme(href_raw.strip())
    if _is_external_href(href_s):
        return _KEEP_LINK, None, href_s
    path_part, _, anchor = href_s.partition("#")
    anchor = anchor or None
    m = V8HELP_RE.match(href_s)
    if m:
        target, anchor = _v8help_link_target(
            m, anchor, archive_index, archive_lookup_final
        )
    else:
        target = _relative_link_target(
            path_part, page_source_path, archive_index, archive_lookup_final
        )
    return target, anchor, href_s


def _is_primitive_target(target_filename: str) -> bool:
    stem = target_filename[:-3] if target_filename.endswith(".md") else target_filename
    stem = stem.rsplit("/", 1)[-1]
    return stem in PRIMITIVE_TYPE_STEMS or stem.startswith("lang__def_")


def rewrite_links(
    soup: BeautifulSoup,
    archive_index: dict[str, str],
    unresolved_log: list[tuple[str, str]],
    page_source_path: str,
    archive_lookup_final: dict[str, str],
) -> None:
    for a in list(soup.find_all("a")):
        if a.has_attr("data-broken-href"):
            a.replace_with(NavigableString(a.get_text("", strip=False)))
            continue
        href = a.get("href")
        if not href:
            continue
        target, anchor, href_s = _link_target(
            href, page_source_path, archive_index, archive_lookup_final
        )
        if target is _KEEP_LINK:
            continue
        if not target:
            _strip_link(a, unresolved_log, page_source_path, href_s)
            continue
        if _is_primitive_target(target):
            a.replace_with(NavigableString(a.get_text("", strip=False)))
            continue
        a["href"] = target + ("#" + anchor if anchor else "")


# ---------- Markdown -----------------------------------------------------

def escape_markdown_link_text(text: str) -> str:
    return text.replace("\\", r"\\").replace("[", r"\[").replace("]", r"\]")


def _escape_angles_in_link_text(md: str) -> str:
    parts = []
    for chunk in CODE_FENCE_RE.split(md):
        if chunk.startswith("```"):
            parts.append(chunk)
            continue
        parts.append(MD_LINK_TEXT_RE.sub(
            lambda m: "[" + m.group(1).replace("<", r"\<").replace(">", r"\>") + "](",
            chunk,
        ))
    return "".join(parts)


def to_markdown(soup: BeautifulSoup) -> str:
    body = soup.body if soup.body else soup
    html = body.decode_contents() if hasattr(body, "decode_contents") else str(body)
    md = md_convert(html, strip=["script", "style"], heading_style="ATX", bullets="-")
    md = _escape_angles_in_link_text(md)
    return cleanup_markdown_noise(md)


def cleanup_markdown_noise(md: str) -> str:
    lines = md.splitlines()
    cleaned: list[str] = []
    for line in lines:
        if "1centerprise.com/devlinks" in line:
            continue
        if METHODOLOGICAL_LINE_RE.match(line):
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip() + "\n"


def write_md(out_dir: Path, filename: str, body: str) -> None:
    (out_dir / filename).write_text(body.strip() + "\n", encoding="utf-8")


# ---------- Индекс архива ------------------------------------------------

def _collect_title_pairs(
    extracted_dir: Path,
    title_map: dict[str, str],
) -> tuple[list[tuple[str, str]], Counter]:
    pairs: list[tuple[str, str]] = []
    title_counts: Counter[str] = Counter()
    for html in iter_html(extracted_dir):
        rel = html.relative_to(extracted_dir).as_posix()
        title = title_map.get(rel.lower(), "")
        pairs.append((rel, title))
        if title:
            title_counts[title] += 1
    return pairs, title_counts


def _title_target(
    rel: str,
    title: str,
    prefix: str,
    title_counts: Counter,
    title_map: dict[str, str],
) -> str:
    target = ""
    if title:
        m = PAGETITLE_SPLIT_RE.match(title)
        ru_title = m.group(1).strip() if m else title
        if title_counts[title] > 1:
            parent = resolve_parent_title(rel, title_map)
            if parent:
                mp = PAGETITLE_SPLIT_RE.match(parent)
                ru_parent = mp.group(1).strip() if mp else parent
                target = title_to_filename(ru_parent + "." + ru_title, prefix=prefix)
            else:
                target = archive_path_to_filename(rel, prefix=prefix)
        else:
            target = title_to_filename(ru_title, prefix=prefix)
    if not target:
        target = archive_path_to_filename(rel, prefix=prefix)
    return target


def build_archive_index(
    extracted_dir: Path,
    prefix: str,
    title_map: dict[str, str] | None = None,
) -> dict[str, str]:
    index: dict[str, str] = {}

    def _add(rel: str, target: str) -> None:
        index[rel.lower()] = target
        if prefix:
            stem = rel.rsplit(".", 1)[0]
            index[stem.lower()] = target

    if title_map is not None:
        pairs, title_counts = _collect_title_pairs(extracted_dir, title_map)
        for rel, title in pairs:
            _add(rel, _title_target(rel, title, prefix, title_counts, title_map))
    else:
        for html in iter_html(extracted_dir):
            rel = html.relative_to(extracted_dir).as_posix()
            _add(rel, archive_path_to_filename(rel, prefix=prefix))
    return index


# ---------- Статистика и результат --------------------------------------

@dataclass
class ConvertStats:
    total: int = 0
    converted: int = 0
    failed: int = 0
    truncated: int = 0
    collisions: int = 0
    unresolved: int = 0


@dataclass
class ConvertResult:
    files: list[Path]
    stats: ConvertStats


# ---------- Конвертер ----------------------------------------------------

ProgressFn = Callable[[str, str], None]


class HbkConverter:
    """Единый rich-конвертер: извлекает FileStorage всех источников и пишет .md."""

    def __init__(
        self,
        sources: list[SourceSpec],
        out_dir: Path,
        on_progress: ProgressFn | None = None,
    ) -> None:
        self.sources = sources
        self.out_dir = out_dir
        self.on_progress = on_progress or (lambda stage, msg: None)
        self.used_names: set[str] = set()
        self.archive_lookup_final: dict[str, str] = {}
        self.stats = ConvertStats()

    def _emit(self, stage: str, message: str) -> None:
        self.on_progress(stage, message)

    def run(self) -> ConvertResult:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        produced: list[Path] = []
        with tempfile.TemporaryDirectory(prefix="v8help-") as tmp:
            tmp_root = Path(tmp)
            archive_index: dict[str, str] = {}
            for src in self.sources:
                ex = tmp_root / src.id
                self._emit("extract", f"Распаковка {src.id}")
                self._extract(src, ex)
                title_map = quick_scan_titles(ex)
                archive_index.update(build_archive_index(ex, src.prefix, title_map))
            for src in self.sources:
                ex = tmp_root / src.id
                produced.extend(self._convert_archive(src, ex, archive_index))
        self.stats.unresolved = 0
        return ConvertResult(produced, self.stats)

    def _extract(self, src: SourceSpec, dest: Path) -> None:
        dest.mkdir(parents=True, exist_ok=True)
        with src.hbk.open("rb") as f:
            zf = HbkContainer(f).file_storage()
            try:
                zf.extractall(dest)
            finally:
                zf.close()

    def _convert_archive(
        self, src: SourceSpec, extracted_dir: Path, archive_index: dict[str, str]
    ) -> list[Path]:
        produced: list[Path] = []
        pages = list(iter_html(extracted_dir))
        total = len(pages)
        report_every = max(1, min(500, total // 10)) if total else 1
        for i, html_path in enumerate(pages):
            rel = html_path.relative_to(extracted_dir).as_posix()
            self.stats.total += 1
            try:
                name = self._convert_one(html_path, rel, archive_index, src)
                self.stats.converted += 1
                produced.append(self.out_dir / name)
            except Exception:
                self.stats.failed += 1
            if (i + 1) % report_every == 0 or (i + 1) == total:
                self._emit("convert", f"{src.id}: {i + 1}/{total}")
        return produced

    def _page_filename(
        self, rel_path: str, archive_index: dict[str, str], src: SourceSpec
    ) -> str:
        target = archive_index.get(rel_path.lower()) or archive_path_to_filename(
            rel_path, prefix=src.prefix
        )
        final_name, was_truncated = truncate_filename(target, rel_path)
        if was_truncated:
            self.stats.truncated += 1
        final_name, was_collision = disambiguate(final_name, self.used_names)
        if was_collision:
            self.stats.collisions += 1
        self.used_names.add(final_name)
        return final_name

    def _drop_duplicate_titles(self, soup: BeautifulSoup, pagetitle_text: str) -> None:
        title_el = soup.find(class_="V8SH_title")
        if title_el and pagetitle_text and title_el.get_text(strip=True) == pagetitle_text:
            title_el.decompose()

    def _page_markdown(
        self, soup: BeautifulSoup, rel_path: str, archive_index: dict[str, str]
    ) -> str:
        unresolved: list[tuple[str, str]] = []
        rewrite_links(soup, archive_index, unresolved, rel_path, self.archive_lookup_final)
        self.stats.unresolved += len(unresolved)
        title_ru, title_en = extract_titles(soup)

        pagetitle_el = soup.find(class_="V8SH_pagetitle") or soup.find("h1")
        pagetitle_text = pagetitle_el.get_text(strip=True) if pagetitle_el else ""
        if pagetitle_el:
            pagetitle_el.decompose()
        self._drop_duplicate_titles(soup, pagetitle_text)

        body = to_markdown(soup)
        heading = title_ru or title_en
        if heading:
            body = f"# {heading}\n\n" + body.lstrip()
        return body

    def _register_lookup(self, rel_path: str, src: SourceSpec, final_name: str) -> None:
        rel_lower = rel_path.lower()
        self.archive_lookup_final[rel_lower] = final_name
        if src.prefix:
            stem = rel_lower.rsplit(".", 1)[0]
            self.archive_lookup_final[stem] = final_name

    def _convert_one(
        self,
        html_path: Path,
        rel_path: str,
        archive_index: dict[str, str],
        src: SourceSpec,
    ) -> str:
        final_name = self._page_filename(rel_path, archive_index, src)
        soup = parse_html(read_html(html_path))
        body = self._page_markdown(soup, rel_path, archive_index)
        write_md(self.out_dir, final_name, body)
        self._register_lookup(rel_path, src, final_name)
        return final_name


# ---------- Консолидация -------------------------------------------------

def consolidate(config, on_progress: ProgressFn | None = None) -> list[Path]:
    """Генерирует md-корпус из [[sources]]/books в corpus_dir. Идемпотентно."""
    sources = config.resolve_sources()
    corpus = config.corpus_dir
    corpus.mkdir(parents=True, exist_ok=True)
    if not sources:
        return []
    # Убираем старые .md, чтобы не оставлять устаревшие страницы.
    for stale in corpus.glob("*.md"):
        stale.unlink()
    converter = HbkConverter(sources, corpus, on_progress)
    result = converter.run()
    return result.files

"""FTS5-поиск: двухуровневый (title -> body) с лестничным смягчением.

Буст заголовков достигается не дублированием title в индексе, а явным
двухуровневым поиском: сначала точные совпадения по колонке ``title``, затем
мягкий поиск по ``body`` (добивка до ``limit``). Внутри — лестница от строгого
к мягкому: NEAR -> AND -> back-off (удаление лишнего токена) -> OR -> префикс.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from v8help import lex
from v8help.search.base import SearchResult

_LIKE_SCORE = 1e9

MAX_AND_TOKENS = 4

# Стоп-слова: предлоги/союзы/частицы/местоимения. Убираются из запроса только
# тогда, когда после их удаления остаётся хотя бы один значимый токен, чтобы не
# зацепить одиночные термины языка (операторы Если/Для/Пока и т.п. не включены).
_STOP_WORDS = frozenset({
    # предлоги
    "на", "в", "во", "с", "со", "к", "ко", "у", "о", "об", "от", "до", "из",
    "без", "при", "под", "над", "за", "перед", "через", "между", "около",
    "после", "против", "среди", "для", "по", "вне", "кроме", "вместо",
    # местоимения
    "я", "ты", "он", "она", "оно", "мы", "вы", "они", "меня", "тебя", "его",
    "ее", "её", "нас", "вас", "их", "мне", "тебе", "ему", "ей", "нам", "вам",
    "им", "мой", "моя", "моё", "мои", "твой", "твоя", "твоё", "твои", "наш",
    "наша", "наше", "наши", "ваш", "ваша", "ваше", "ваши", "себя", "себе",
    "собой", "этот", "эта", "это", "эти", "тот", "та", "то", "те", "такой",
    "такая", "такое", "такие", "весь", "вся", "всё", "все", "что", "кто",
    "какой", "какая", "какое", "какие", "чей", "где", "куда", "откуда",
    "когда", "зачем", "почему", "сколько", "который", "которая", "которое",
    "которые",
    # частицы
    "бы", "же", "ли", "не", "ни", "лишь", "только", "даже", "вот", "вон",
    "ведь", "уж", "неужели", "разве", "будто",
    # союзы
    "а", "но", "да", "тоже", "также", "зато", "однако", "хотя", "чтобы",
    "чтоб", "так", "тогда", "потом",
    # английские
    "a", "an", "the", "and", "or", "of", "in", "on", "at", "to", "for",
    "with", "by", "from", "is", "are", "was", "were", "be", "been", "being",
    "it", "its", "this", "that", "these", "those", "as", "not", "but", "if",
    "then", "else", "than", "so", "do", "does", "did", "will", "would",
    "can", "could", "should", "may", "might", "must", "i", "you", "he",
    "she", "we", "they", "them", "their", "his", "her", "our", "your",
    "my", "me", "us",
})


def _tokenize(query: str) -> list[str]:
    """Расширяет PascalCase, режет на токены, приводит к lowercase, убирает стоп-слова."""
    expanded = lex.expand(query)
    raw: list[str] = []
    for token in expanded.split():
        clean = "".join(c if c.isalnum() else " " for c in token)
        raw.extend(t for t in clean.split() if t)

    tokens: list[str] = []
    for t in raw:
        t = t.lower()
        if t not in tokens:
            tokens.append(t)

    if len(tokens) <= 1:
        return tokens
    significant = [t for t in tokens if t not in _STOP_WORDS]
    return significant if significant else tokens


def _and_q_field(field: str, tokens: list[str]) -> str:
    return f"{field}:({' AND '.join(tokens)})"


def _or_q_field(field: str, tokens: list[str]) -> str:
    return f"{field}:({' OR '.join(tokens)})"


def _near_q_field(field: str, tokens: list[str], dist: int) -> str:
    return f"{field}:NEAR({' '.join(tokens)}, {dist})"


def _snippet(text: str, term: str, width: int = 120) -> str | None:
    idx = text.lower().find(term.lower())
    if idx < 0:
        return None
    start = max(0, idx - width // 3)
    end = min(len(text), idx + len(term) + (width * 2) // 3)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return (prefix + text[start:end] + suffix).strip()


def _make_snippet(body: str, title: str, query: str) -> str:
    tokens = [t for t in query.split() if t]
    for t in tokens:
        snip = _snippet(body, t)
        if snip:
            return snip
    for t in tokens:
        snip = _snippet(title, t)
        if snip:
            return snip
    return (body[:120].strip() or title).strip()


class FtsBackend:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def search(
        self,
        query: str,
        limit: int = 10,
        section: str | None = None,
        kind: str | None = None,
    ) -> list[SearchResult]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            if not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pages'"
            ).fetchone():
                return []

            tokens = _tokenize(query)
            if not tokens:
                return self._substring_search(
                    conn, query, limit, section, kind, set(), in_body=True
                )

            if len(tokens) > MAX_AND_TOKENS:
                df = {t: self._count(conn, t, None, None) for t in tokens}
                tokens = sorted(tokens, key=lambda t: (df[t], t))[:MAX_AND_TOKENS]

            results: list[SearchResult] = []
            seen: set[str] = set()

            # Уровень 1 — точные совпадения в заголовке.
            results += self._search_title(
                conn, tokens, query, limit, section, kind, seen
            )

            # Уровень 2 — мягкий поиск по телу (добивка).
            remaining = limit - len(results)
            if remaining > 0:
                results += self._search_body(
                    conn, tokens, query, remaining, section, kind, seen
                )

            # LIKE-fallback: сначала заголовок, потом тело.
            remaining = limit - len(results)
            if remaining > 0:
                results += self._substring_search(
                    conn, query, remaining, section, kind, seen, in_body=False
                )
            remaining = limit - len(results)
            if remaining > 0:
                results += self._substring_search(
                    conn, query, remaining, section, kind, seen, in_body=True
                )

            return results
        finally:
            conn.close()

    def _search_title(self, conn, tokens, query, limit, section, kind, seen):
        """Только строгие совпадения по заголовку: NEAR, AND, back-off."""
        out: list[SearchResult] = []

        def add(fts_q: str) -> None:
            rem = limit - len(out)
            if rem > 0:
                out.extend(
                    self._fts_search(conn, fts_q, query, rem, section, kind, seen)
                )

        if len(tokens) >= 2:
            add(_near_q_field("title", tokens, len(tokens) * 5 + 3))
        add(_and_q_field("title", tokens))
        if len(tokens) >= 2 and len(out) < limit:
            out.extend(
                self._backoff(
                    conn, tokens, query, limit - len(out), section, kind, seen,
                    "title",
                )
            )
        return out

    def _search_body(self, conn, tokens, query, limit, section, kind, seen):
        """Полная лестница по телу: NEAR, AND, back-off, OR, префикс."""
        out: list[SearchResult] = []

        def add(fts_q: str) -> None:
            rem = limit - len(out)
            if rem > 0:
                out.extend(
                    self._fts_search(conn, fts_q, query, rem, section, kind, seen)
                )

        if len(tokens) >= 2:
            add(_near_q_field("body", tokens, len(tokens) * 5 + 3))
        add(_and_q_field("body", tokens))
        if len(tokens) >= 2 and len(out) < limit:
            out.extend(
                self._backoff(
                    conn, tokens, query, limit - len(out), section, kind, seen,
                    "body",
                )
            )
        add(_or_q_field("body", tokens))
        add(_or_q_field("body", [t + "*" for t in tokens]))
        return out

    def _backoff(self, conn, tokens, query, limit, section, kind, seen, field):
        """Удаляет один токен, выбирая удаление с максимумом ненулевых совпадений."""
        best_sub = None
        best_count = 0
        best_q = None
        for i in range(len(tokens)):
            sub = tokens[:i] + tokens[i + 1:]
            q = _and_q_field(field, sub)
            c = self._count(conn, q, section, kind)
            if c > best_count:
                best_count, best_sub, best_q = c, sub, q
        if best_count > 0:
            return self._fts_search(conn, best_q, query, limit, section, kind, seen)
        return []

    def _count(self, conn, fts_q, section, kind) -> int:
        conds = ["pages_fts MATCH ?"]
        params: list = [fts_q]
        if section:
            conds.append("p.section = ?")
            params.append(section)
        if kind:
            conds.append("p.kind = ?")
            params.append(kind)
        sql = (
            "SELECT count(*) FROM pages_fts JOIN pages p ON p.id = pages_fts.rowid"
            " WHERE " + " AND ".join(conds)
        )
        return conn.execute(sql, params).fetchone()[0]

    def _fts_search(self, conn, fts_q, query, limit, section, kind, seen):
        conds = ["pages_fts MATCH ?"]
        params: list = [fts_q]
        if section:
            conds.append("p.section = ?")
            params.append(section)
        if kind:
            conds.append("p.kind = ?")
            params.append(kind)
        params.append(limit)
        sql = (
            "SELECT p.filename, p.title, p.section, p.kind, p.body,"
            " bm25(pages_fts) AS score"
            " FROM pages_fts JOIN pages p ON p.id = pages_fts.rowid"
            " WHERE " + " AND ".join(conds) + " ORDER BY bm25(pages_fts) LIMIT ?"
        )
        out: list[SearchResult] = []
        for row in conn.execute(sql, params):
            if row["filename"] in seen:
                continue
            seen.add(row["filename"])
            out.append(
                SearchResult(
                    id=row["filename"],
                    title=row["title"],
                    snippet=_make_snippet(row["body"], row["title"], query),
                    source_path=row["filename"],
                    section=row["section"],
                    kind=row["kind"],
                    score=row["score"],
                )
            )
        return out

    def _substring_search(self, conn, query, limit, section, kind, seen, in_body):
        tokens = [t.lower() for t in query.split() if t]
        if not tokens:
            return []
        conds = []
        params: list = []
        if section:
            conds.append("section = ?")
            params.append(section)
        if kind:
            conds.append("kind = ?")
            params.append(kind)
        sql = "SELECT filename, title, section, kind, body FROM pages"
        if conds:
            sql += " WHERE " + " AND ".join(conds)

        out: list[SearchResult] = []
        for row in conn.execute(sql, params):
            if row["filename"] in seen:
                continue
            title_l = row["title"].lower()
            if all(t in title_l for t in tokens):
                snip = row["title"]
            elif in_body and all(t in row["body"].lower() for t in tokens):
                snip = _snippet(row["body"], tokens[0]) or row["title"]
            else:
                continue
            seen.add(row["filename"])
            out.append(
                SearchResult(
                    id=row["filename"],
                    title=row["title"],
                    snippet=snip,
                    source_path=row["filename"],
                    section=row["section"],
                    kind=row["kind"],
                    score=_LIKE_SCORE,
                )
            )
            if len(out) >= limit:
                break
        return out

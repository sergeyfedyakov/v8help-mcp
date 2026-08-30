"""FTS5-поиск по чанкам: двухуровневый с весами колонок.

``chunks_fts`` содержит колонки ``title`` / ``description`` / ``body`` (секция
«Описание:» статьи). Поиск двухуровневый:

1. Строгие совпадения в заголовке (``title:AND``/``NEAR``/back-off) — точные
   названия статей («Глобальный контекст») остаются в топе несмотря на низкий
   idf распространённых слов.
2. Единый мягкий запрос по всем колонкам с ранжированием
   ``bm25(chunks_fts, 9.0, 3.0, 1.0)``: совпадение в заголовке в 9 раз
   весомее телесного, в описании — в 3 раза.

Внутри лестницы — от строгого к мягкому: NEAR -> AND -> back-off (удаление
лишнего токена) -> OR -> префикс.

Единица поиска — чанк (``chunks``/``chunks_fts``); результат несёт метаданные
родителя (страницы). Дедупликация по родителю — не более ``max_chunks_per_page``
чанков одной статьи в выдаче.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from v8help import lex
from v8help.search.base import SearchResult

_LIKE_SCORE = 1e9

MAX_AND_TOKENS = 4

# Веса колонок chunks_fts для bm25: заголовок / описание / тело.
_TITLE_WEIGHT = 9.0
_DESCRIPTION_WEIGHT = 3.0
_BODY_WEIGHT = 1.0

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


def _and_q(tokens: list[str]) -> str:
    return "(" + " AND ".join(tokens) + ")"


def _or_q(tokens: list[str]) -> str:
    return "(" + " OR ".join(tokens) + ")"


def _near_q(tokens: list[str], dist: int) -> str:
    return f"NEAR({' '.join(tokens)}, {dist})"


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
    def __init__(
        self,
        db_path: str | Path,
        max_chunks_per_page: int = 2,
    ) -> None:
        self.db_path = Path(db_path)
        self.max_chunks_per_page = max_chunks_per_page

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
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chunks_fts'"
            ).fetchone():
                return []

            tokens = _tokenize(query)
            if not tokens:
                return self._substring_search(
                    conn, query, limit, section, kind, in_body=True
                )

            if len(tokens) > MAX_AND_TOKENS:
                df = {t: self._count(conn, t, None, None) for t in tokens}
                tokens = sorted(tokens, key=lambda t: (df[t], t))[:MAX_AND_TOKENS]

            results: list[SearchResult] = []

            # Уровень 1 — строгие совпадения в заголовке: точное название
            # статьи («Глобальный контекст», «Журнал регистрации») должно быть
            # в топе, несмотря на низкий idf распространённых слов.
            results += self._search_title(
                conn, tokens, query, limit, section, kind
            )

            # Уровень 2 — единый запрос по всем колонкам (title/description/body),
            # ранжирование bm25 с весами колонок; лестница смягчения ниже.
            remaining = limit - len(results)
            if remaining > 0:
                results += self._search(
                    conn, tokens, query, remaining, section, kind
                )

            # LIKE-fallback: сначала заголовок, потом тело.
            remaining = limit - len(results)
            if remaining > 0:
                results += self._substring_search(
                    conn, query, remaining, section, kind, in_body=False
                )
            remaining = limit - len(results)
            if remaining > 0:
                results += self._substring_search(
                    conn, query, remaining, section, kind, in_body=True
                )

            return self._dedup(results)
        finally:
            conn.close()

    def _dedup(self, results: list[SearchResult]) -> list[SearchResult]:
        """Схлопывает одинаковые чанки и не допускает больше
        ``max_chunks_per_page`` чанков одной статьи в выдаче."""
        out: list[SearchResult] = []
        seen_chunks: set[int] = set()
        seen_pages: dict[str, int] = {}
        for r in results:
            if r.chunk_id and r.chunk_id in seen_chunks:
                continue
            if self.max_chunks_per_page > 0:
                used = seen_pages.get(r.id, 0)
                if used >= self.max_chunks_per_page:
                    continue
                seen_pages[r.id] = used + 1
            if r.chunk_id:
                seen_chunks.add(r.chunk_id)
            out.append(r)
        return out

    def _search_title(self, conn, tokens, query, limit, section, kind):
        """Только строгие совпадения по заголовку: NEAR, AND, back-off."""
        out: list[SearchResult] = []

        def add(fts_q: str) -> None:
            rem = limit - len(out)
            if rem > 0:
                out.extend(
                    self._fts_search(conn, fts_q, query, rem, section, kind)
                )

        if len(tokens) >= 2:
            add(f"title:{_near_q(tokens, len(tokens) * 5 + 3)}")
        add(f"title:{_and_q(tokens)}")
        if len(tokens) >= 2 and len(out) < limit:
            out.extend(
                self._backoff(
                    conn, tokens, query, limit - len(out), section, kind,
                    "title",
                )
            )
        return out

    def _search(self, conn, tokens, query, limit, section, kind):
        """Полная лестница по всем колонкам: NEAR, AND, back-off, OR, префикс."""
        out: list[SearchResult] = []

        def add(fts_q: str) -> None:
            rem = limit - len(out)
            if rem > 0:
                out.extend(
                    self._fts_search(conn, fts_q, query, rem, section, kind)
                )

        if len(tokens) >= 2:
            add(_near_q(tokens, len(tokens) * 5 + 3))
        add(_and_q(tokens))
        if len(tokens) >= 2 and len(out) < limit:
            out.extend(
                self._backoff(
                    conn, tokens, query, limit - len(out), section, kind,
                )
            )
        add(_or_q(tokens))
        add(_or_q([t + "*" for t in tokens]))
        return out

    def _backoff(self, conn, tokens, query, limit, section, kind, field=None):
        """Удаляет один токен, выбирая удаление с максимумом ненулевых совпадений."""
        best_sub = None
        best_count = 0
        best_q = None
        for i in range(len(tokens)):
            sub = tokens[:i] + tokens[i + 1:]
            q = _and_q(sub)
            if field:
                q = f"{field}:{q}"
            c = self._count(conn, q, section, kind)
            if c > best_count:
                best_count, best_sub, best_q = c, sub, q
        if best_count > 0:
            return self._fts_search(conn, best_q, query, limit, section, kind)
        return []

    def _count(self, conn, fts_q, section, kind) -> int:
        conds = ["chunks_fts MATCH ?"]
        params: list = [fts_q]
        if section:
            conds.append("p.section = ?")
            params.append(section)
        if kind:
            conds.append("p.kind = ?")
            params.append(kind)
        sql = (
            "SELECT count(*) FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid"
            " JOIN pages p ON p.id = c.page_id"
            " WHERE " + " AND ".join(conds)
        )
        return conn.execute(sql, params).fetchone()[0]

    def _fts_search(self, conn, fts_q, query, limit, section, kind):
        conds = ["chunks_fts MATCH ?"]
        params: list = [fts_q]
        if section:
            conds.append("p.section = ?")
            params.append(section)
        if kind:
            conds.append("p.kind = ?")
            params.append(kind)
        params.append(limit)
        weights = (
            f"bm25(chunks_fts, {_TITLE_WEIGHT}, {_DESCRIPTION_WEIGHT}, {_BODY_WEIGHT})"
        )
        sql = (
            "SELECT p.filename, p.title, p.section, p.kind, c.id AS chunk_id,"
            " c.chunk_index, c.title AS chunk_title, c.body AS chunk_body,"
            " (SELECT COUNT(*) FROM chunks cc WHERE cc.page_id = p.id) AS total,"
            f" {weights} AS score"
            " FROM chunks_fts"
            " JOIN chunks c ON c.id = chunks_fts.rowid"
            " JOIN pages p ON p.id = c.page_id"
            " WHERE " + " AND ".join(conds)
            + f" ORDER BY {weights} LIMIT ?"
        )
        out: list[SearchResult] = []
        for row in conn.execute(sql, params):
            out.append(
                SearchResult(
                    id=row["filename"],
                    title=row["title"],
                    snippet=_make_snippet(row["chunk_body"], row["chunk_title"], query),
                    source_path=row["filename"],
                    section=row["section"],
                    kind=row["kind"],
                    score=row["score"],
                    chunk_id=row["chunk_id"],
                    chunk_index=row["chunk_index"],
                    total_chunks=row["total"],
                    chunk_title=row["chunk_title"],
                )
            )
        return out

    def _substring_search(self, conn, query, limit, section, kind, in_body):
        tokens = [t.lower() for t in query.split() if t]
        if not tokens:
            return []
        conds = []
        params: list = []
        if section:
            conds.append("p.section = ?")
            params.append(section)
        if kind:
            conds.append("p.kind = ?")
            params.append(kind)
        sql = (
            "SELECT p.filename, p.title, p.section, p.kind, c.id AS chunk_id,"
            " c.chunk_index, c.title AS chunk_title, c.body AS chunk_body,"
            " (SELECT description FROM chunks_fts WHERE rowid = c.id) AS chunk_description,"
            " (SELECT COUNT(*) FROM chunks cc WHERE cc.page_id = p.id) AS total"
            " FROM chunks c JOIN pages p ON p.id = c.page_id"
        )
        if conds:
            sql += " WHERE " + " AND ".join(conds)

        out: list[SearchResult] = []
        for row in conn.execute(sql, params):
            title_l = row["title"].lower()
            if all(t in title_l for t in tokens):
                snip = row["title"]
            elif in_body and all(t in row["chunk_body"].lower() for t in tokens):
                snip = _snippet(row["chunk_body"], tokens[0]) or row["title"]
            elif in_body and all(
                t in (row["chunk_description"] or "").lower() for t in tokens
            ):
                snip = _snippet(row["chunk_description"], tokens[0]) or row["title"]
            else:
                continue
            out.append(
                SearchResult(
                    id=row["filename"],
                    title=row["title"],
                    snippet=snip,
                    source_path=row["filename"],
                    section=row["section"],
                    kind=row["kind"],
                    score=_LIKE_SCORE,
                    chunk_id=row["chunk_id"],
                    chunk_index=row["chunk_index"],
                    total_chunks=row["total"],
                    chunk_title=row["chunk_title"],
                )
            )
            if len(out) >= limit:
                break
        return out

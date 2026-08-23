"""FTS5-поиск (вариант A): MATCH + bm25 + LIKE-fallback по подстроке."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from v8help import lex
from v8help.search.base import SearchResult

_LIKE_SCORE = 1e9


def _fts_query(query: str) -> str:
    expanded = lex.expand(query)
    tokens: list[str] = []
    for token in expanded.split():
        clean = "".join(c if c.isalnum() or c == "_" else " " for c in token)
        tokens.extend(t for t in clean.split() if t)
    return " ".join(tokens)


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

            results: list[SearchResult] = []
            seen: set[str] = set()

            fts_q = _fts_query(query)
            if fts_q:
                results = self._fts_search(conn, fts_q, query, limit, section, kind, seen)

            if len(results) < limit:
                results += self._substring_search(
                    conn, query, limit - len(results), section, kind, seen, in_body=False
                )
            if len(results) < limit:
                results += self._substring_search(
                    conn, query, limit - len(results), section, kind, seen, in_body=True
                )
            return results
        finally:
            conn.close()

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

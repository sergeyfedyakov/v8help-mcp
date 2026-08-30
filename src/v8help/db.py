"""Слой доступа к SQLite (pages / chunks / links / vectors)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pages (
    id          INTEGER PRIMARY KEY,
    filename    TEXT NOT NULL UNIQUE,
    title       TEXT NOT NULL,
    section     TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT 'page',
    hbk_source  TEXT NOT NULL DEFAULT '',
    source_path TEXT NOT NULL DEFAULT '',
    body        TEXT NOT NULL DEFAULT '',
    search_text TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS chunks (
    id          INTEGER PRIMARY KEY,
    page_id     INTEGER NOT NULL REFERENCES pages(id),
    chunk_index INTEGER NOT NULL,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    chars       INTEGER NOT NULL,
    src         TEXT NOT NULL DEFAULT 'page',
    prev_id     INTEGER,
    next_id     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_chunks_page ON chunks(page_id, chunk_index);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    title,
    description,
    body,
    tokenize='unicode61'
);

CREATE TABLE IF NOT EXISTS links (
    src TEXT NOT NULL,
    dst TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_links_src ON links(src);
CREATE INDEX IF NOT EXISTS idx_links_dst ON links(dst);

CREATE TABLE IF NOT EXISTS vectors (
    chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id),
    vec     BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS embed_queue (
    chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id),
    title   TEXT NOT NULL,
    body    TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def exists(self) -> bool:
        if not self.path.exists():
            return False
        conn = self.connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pages'"
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def reset(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            "DROP TABLE IF EXISTS pages;"
            "DROP TABLE IF EXISTS chunks;"
            "DROP TABLE IF EXISTS chunks_fts;"
            "DROP TABLE IF EXISTS links;"
            "DROP TABLE IF EXISTS vectors;"
            "DROP TABLE IF EXISTS embed_queue;"
            "DROP TABLE IF EXISTS meta;"
        )
        conn.executescript(SCHEMA)

    def insert_page(
        self,
        conn: sqlite3.Connection,
        filename: str,
        title: str,
        section: str,
        kind: str,
        hbk_source: str,
        source_path: str,
        body: str,
        title_search: str,
        body_search: str,
    ) -> int:
        cur = conn.execute(
            "INSERT INTO pages(filename,title,section,kind,hbk_source,source_path,body,search_text)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (filename, title, section, kind, hbk_source, source_path, body,
             title_search + "\n" + body_search),
        )
        return cur.lastrowid

    def insert_chunk(
        self,
        conn: sqlite3.Connection,
        page_id: int,
        chunk_index: int,
        title: str,
        body: str,
        src: str = "page",
        description: str = "",
    ) -> int:
        cur = conn.execute(
            "INSERT INTO chunks(page_id,chunk_index,title,body,chars,src)"
            " VALUES(?,?,?,?,?,?)",
            (page_id, chunk_index, title, body, len(body), src),
        )
        chunk_id = cur.lastrowid
        conn.execute(
            "INSERT INTO chunks_fts(rowid, title, description, body) VALUES(?,?,?,?)",
            (chunk_id, title, description, body),
        )
        return chunk_id

    def link_chunks(
        self,
        conn: sqlite3.Connection,
        chunks: list[int],
    ) -> None:
        """Проставляет prev_id/next_id для последовательности чанков страницы."""
        for i, cid in enumerate(chunks):
            prev = chunks[i - 1] if i > 0 else None
            nxt = chunks[i + 1] if i + 1 < len(chunks) else None
            conn.execute(
                "UPDATE chunks SET prev_id=?, next_id=? WHERE id=?",
                (prev, nxt, cid),
            )

    def insert_links(self, conn: sqlite3.Connection, src: str, dsts: list[str]) -> None:
        conn.executemany(
            "INSERT INTO links(src,dst) VALUES(?,?)",
            [(src, d) for d in dsts],
        )

    def insert_vector(self, conn: sqlite3.Connection, chunk_id: int, vec: bytes) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO vectors(chunk_id, vec) VALUES(?,?)",
            (chunk_id, vec),
        )

    def insert_vectors(self, conn: sqlite3.Connection, rows: list[tuple[int, bytes]]) -> None:
        conn.executemany(
            "INSERT OR REPLACE INTO vectors(chunk_id, vec) VALUES(?,?)",
            rows,
        )

    def enqueue_chunks(
        self, conn: sqlite3.Connection, rows: list[tuple[int, str, str]]
    ) -> None:
        """План эмбеддинга: (chunk_id, title, body) для ещё не обработанных чанков."""
        conn.executemany(
            "INSERT OR REPLACE INTO embed_queue(chunk_id, title, body) VALUES(?,?,?)",
            rows,
        )

    def dequeue_chunks(self, conn: sqlite3.Connection, chunk_ids: list[int]) -> None:
        """Снимает обработанные чанки с плана (после записи векторов)."""
        conn.executemany(
            "DELETE FROM embed_queue WHERE chunk_id=?",
            [(cid,) for cid in chunk_ids],
        )

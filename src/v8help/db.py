"""Слой доступа к SQLite (pages / pages_fts / links)."""

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

CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
    search_text,
    content='pages',
    content_rowid='id',
    tokenize='unicode61'
);

CREATE TABLE IF NOT EXISTS links (
    src TEXT NOT NULL,
    dst TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_links_src ON links(src);
CREATE INDEX IF NOT EXISTS idx_links_dst ON links(dst);
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
            "DROP TABLE IF EXISTS pages_fts;"
            "DROP TABLE IF EXISTS links;"
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
        search_text: str,
    ) -> int:
        cur = conn.execute(
            "INSERT INTO pages(filename,title,section,kind,hbk_source,source_path,body,search_text)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (filename, title, section, kind, hbk_source, source_path, body, search_text),
        )
        rowid = cur.lastrowid
        conn.execute(
            "INSERT INTO pages_fts(rowid, search_text) VALUES(?,?)",
            (rowid, search_text),
        )
        return rowid

    def insert_links(self, conn: sqlite3.Connection, src: str, dsts: list[str]) -> None:
        conn.executemany(
            "INSERT INTO links(src,dst) VALUES(?,?)",
            [(src, d) for d in dsts],
        )

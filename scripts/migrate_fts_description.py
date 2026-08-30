"""Разовый скрипт: добавить колонку description в chunks_fts существующей БД.

Не пересчитывает эмбеддинги и не трогает pages/chunks/vectors — только
пересоздаёт FTS-индекс (chunks_fts) с новой схемой ``fts5(title, description,
body)``, заполняя description из секции «Описание:» каждой страницы.

В CLI не добавляется: запуск вручную, один раз на существующую БД.
    python scripts/migrate_fts_description.py [db_path] [--force]
По умолчанию db_path = data/v8help.db. Перед миграцией делается резервная копия.
--force — пересоздать chunks_fts даже если колонка description уже есть
(например, после изменения парсера extract_description).
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
import time
from pathlib import Path

from v8help import metadata


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    force = "--force" in argv
    db_path = args[0] if args else "data/v8help.db"
    db = Path(db_path)
    if not db.exists():
        print(f"БД не найдена: {db}")
        return 2

    backup = db.with_name(db.name + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(db, backup)
    print(f"Резервная копия: {backup}")

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='chunks_fts'"
        ).fetchone()
        if not row:
            print("Таблица chunks_fts не найдена — миграция не нужна")
            return 1
        cols = [r[1] for r in conn.execute("PRAGMA table_info(chunks_fts)")]
        if "description" in cols and not force:
            print("chunks_fts уже содержит description — пропуск (нужен --force)")
            return 0

        descs: dict[int, str] = {}
        for r in conn.execute("SELECT id, body FROM pages"):
            d = metadata.extract_description(r["body"])
            if d:
                descs[r["id"]] = d
        print(f"Извлечено описаний: {len(descs)}")

        conn.execute("BEGIN")
        conn.execute("DROP TABLE IF EXISTS chunks_fts_new")
        conn.executescript(
            "CREATE VIRTUAL TABLE chunks_fts_new USING fts5("
            "    title,"
            "    description,"
            "    body,"
            "    tokenize='unicode61'"
            ");"
        )
        chunks = conn.execute(
            "SELECT c.id, c.title, c.page_id, c.body FROM chunks c"
        ).fetchall()
        conn.executemany(
            "INSERT INTO chunks_fts_new(rowid, title, description, body)"
            " VALUES(?,?,?,?)",
            [
                (r["id"], r["title"], descs.get(r["page_id"], ""), r["body"])
                for r in chunks
            ],
        )
        conn.execute("DROP TABLE chunks_fts")
        conn.execute("ALTER TABLE chunks_fts_new RENAME TO chunks_fts")
        conn.execute("COMMIT")
        print(f"chunks_fts пересоздана: {len(chunks)} строк")
    except Exception as exc:
        conn.rollback()
        print(f"ОШИБКА: {exc}")
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

# Разработка

## Окружение и тесты

```bash
pip install -e ".[dev]"
pytest
```

Тесты прогоняются в **двух окружениях**:

- текущее (Windows, Python 3.13.x);
- Linux — Debian 13 «trixie» (или любой Linux): `python -m venv .venv &&
  . .venv/bin/activate && pip install -e . && pytest`.

Это ловит платформозависимые дефекты (пути, кодировки, sqlite, newline).

## Структура кода

- `src/v8help/` — пакет:
  - `config.py` — конфиг (`v8help.toml`), автодискавери `bin_dir` и эмбеддеров;
  - `unpack/` — чтение `.hbk`-контейнеров (Format15, свой TOC-парсер);
  - `converter.py` — конвертация HTML → Markdown, неймспейсы;
  - `metadata.py` — секции/префиксы страниц, извлечение «Описания»;
  - `db.py` — схема SQLite (pages, chunks, links, vectors, meta);
  - `indexer.py` — сборка корпуса, FTS, чанкование, эмбеддинг;
  - `jobs.py` — асинхронные задачи сборки (JobManager, атомарная подмена БД);
  - `search/` — `fts.py`, `vectors.py`, `hybrid.py` (RRF), `embedder.py`,
    `chunker.py`, `ranking.py`;
  - `server.py` — MCP-сервер (stdio, JSON-RPC 2.0);
  - `cli.py` — командная строка.
- `scripts/` — вспомогательные скрипты:
  - `setup-ollama.ps1` / `setup-ollama.sh` — установка Ollama и модели;
  - `migrate_fts_description.py` — миграция FTS (добавление колонки description);
  - `add_book_incremental.py` — инкрементальное добавление книги `.hbk` в БД.
- `tests/` — pytest-тесты.

## Полезные команды

```bash
v8help build --force            # полная пересборка индекса
```

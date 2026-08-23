# v8help

MCP-инструмент и CLI для чтения, индексации и поиска по файлам справки
1С:Предприятие (`.hbk`).

Извлекает HTML-страницы из V8-контейнера справки, конвертирует их в Markdown,
строит полнотекстовый индекс (SQLite FTS5) и отдаёт поиск через MCP-сервер
(stdio, JSON-RPC 2.0) или командную строку.

## Возможности

- Самодостаточная пересборка корпуса из `.hbk` одной командой `build`
  (распаковка → консолидация → индексация).
- Чтение контейнеров `Format15` с корректным парсингом TOC (включая свободные
  блоки, которые ломают штатный `onec_dtools.read_entries`).
- Единый конвертер HTML → Markdown: заголовки по `V8SH_pagetitle` (синтакс-
  помощник), имена по пути архива (язык запросов и др.), переписывание ссылок
  `v8help://...` в относительные `.md`.
- Полнотекстовый поиск FTS5 с лексическим расширением (разбиение
  PascalCase-идентификаторов, например `СтрНайтиПоРегулярномуВыражению`).
- Асинхронная сборка через MCP: `build` возвращает `job_id` сразу, прогресс —
  через `build_status`; поиск при этом не блокируется (атомарная подмена БД).

## Требования

- Python 3.11+
- Установленная платформа 1С:Предприятие (каталог `bin` с `.hbk`-файлами)

## Установка

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -e .
```

Установка регистрирует два консольных скрипта: `v8help` (CLI) и
`v8help-mcp` (MCP-сервер).

## Конфигурация

Скопируйте пример конфигурации и укажите путь к `bin` вашей платформы:

```bash
copy v8help.example.toml v8help.toml
```

```toml
# v8help.toml
bin_dir = "C:/Program Files/1cv8/8.5.1.1423/bin"
corpus_dir = "data/corpus"
db_path = "data/v8help.db"
lang = "ru"

# Книги по умолчанию (shorthand через bin_dir)
books = ["shcntx_ru", "shlang_ru", "shquery_ru", "shclang_ru"]

# Либо явные источники:
# [[sources]]
# id = "shquery"
# hbk = "C:/Program Files/1cv8/8.5.1.1423/bin/shquery_ru.hbk"
# prefix = "query__"
# scheme = "SyntaxHelperQueries"

[search]
backend = "fts"
limit = 10
```

Встроенные неймспейсы `v8help://<scheme>` → префикс имён:

| scheme                       | prefix    | книга       |
|------------------------------|-----------|-------------|
| `SyntaxHelperContext`        | (пусто)   | `shcntx_*`  |
| `SyntaxHelperLanguage`       | `lang__`  | `shlang_*`  |
| `SyntaxHelperQueries`        | `query__` | `shquery_*` |
| `SyntaxHelperCommonLanguage` | `clang__` | `shclang_*` |

## CLI

```bash
v8help build                       # распаковать .hbk, собрать корпус и индекс
v8help build --sources shquery_ru --force
v8help search "регулярному"        # полнотекстовый поиск
v8help get-page lang__def_String   # полный текст страницы
v8help hierarchy                   # сводка по разделам
v8help related lang__def_String    # связанные страницы
v8help serve                       # MCP-сервер (stdio)
```

## MCP

Подключение в клиенте MCP (например, Kilo) — команда `.venv/Scripts/v8help-mcp.exe`
(или `v8help-mcp` из PATH).

Инструменты:

- `search(query, section?, kind?, limit?)` — полнотекстовый поиск.
- `get_page(id)` — полный текст страницы по имени файла или числовому id.
- `hierarchy(section?)` — оглавление/сводка разделов.
- `related(id)` — входящие и исходящие ссылки страницы.
- `build(sources?, lang?, cleanup?, force?)` — асинхронная пересборка индекса,
  возвращает `job_id`.
- `build_status(job_id)` — статус сборки (`running` / `done` / `error` + прогресс).

## Разработка

```bash
pip install -e ".[dev]"
pytest
```

## Лицензия

MIT — см. [LICENSE](LICENSE).

## Благодарности

Конвертер HTML → Markdown портирован из
[hbk-to-md](https://github.com/pzayash/hbk-to-md).

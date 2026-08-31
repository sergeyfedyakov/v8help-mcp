# Использование: CLI и MCP

Полная справка по командам `v8help` (CLI) и инструментам MCP-сервера.

## CLI

Глобальные опции (указываются до подкоманды):

```bash
v8help --version                 # версия пакета
v8help --config PATH             # путь к TOML-конфигу (по умолчанию v8help.toml)
```

Подкоманды: `build`, `search`, `get-page`, `hierarchy`, `related`, `serve`.

### build

Распаковать `.hbk`, собрать корпус и индекс (FTS + чанки; векторы — если задан
эмбеддер `embedder.index`).

```bash
v8help build [--sources SRC ...] [--lang ru|en] [--cleanup] [--force]
             [--chunk-size N] [--chunk-overlap N]
```

| Параметр         | Описание                                                      |
|------------------|---------------------------------------------------------------|
| `--sources`      | Источники для сборки (по умолчанию все из конфига)           |
| `--lang`         | Язык: `ru`/`en` (по умолчанию из конфига)                    |
| `--cleanup`      | Удалить corpus после индексации                              |
| `--force`        | Пересобрать, даже если индекс актуален                        |
| `--chunk-size`   | Целевой размер чанка в символах (по умолчанию 1500)           |
| `--chunk-overlap`| Перекрытие соседних чанков в символах (по умолчанию 200)      |

### search

Поиск по справке.

```bash
v8help search QUERY [--section SECTION] [--kind KIND] [--limit N]
```

| Параметр   | Описание                                                        |
|------------|-----------------------------------------------------------------|
| `QUERY`    | Поисковый запрос (обязательный)                                 |
| `--section`| Фильтр по разделу: `objects`/`tables`/`lang`/`query`/`clang`     |
| `--kind`   | Фильтр по kind: `page`/`member`/`index`                          |
| `--limit`  | Максимум результатов (по умолчанию из `search.limit` конфига)    |

### get-page

Полный текст страницы.

```bash
v8help get-page ID [--chunk N]
```

| Параметр  | Описание                                                        |
|-----------|-----------------------------------------------------------------|
| `ID`      | Идентификатор страницы: filename (без `.md`) или числовой id    |
| `--chunk` | Номер чанка длинной статьи (0-based); без него — заголовок, метаданные и тело целиком |

### hierarchy

Дерево TOC.

```bash
v8help hierarchy [--section SECTION]
```

Без `--section` — сводка по разделам с количеством страниц; с `--section` — группы
страниц раздела (top-level объекты) с количеством.

### related

Связанные страницы (исходящие и входящие ссылки).

```bash
v8help related ID
```

### serve

Запустить MCP-сервер: stdio (по умолчанию) или streamable-http (`--http`).
Аналог скрипта `v8help-mcp` (stdio).

```bash
v8help serve [--http] [--host HOST] [--port PORT]
```

| Параметр | Описание                                                    |
|----------|-------------------------------------------------------------|
| `--http` | HTTP-транспорт (streamable-http) вместо stdio, путь `/mcp`  |
| `--host` | Адрес для HTTP (по умолчанию `127.0.0.1`)                   |
| `--port` | Порт для HTTP (по умолчанию `8000`)                         |

## MCP

Подключение в клиенте MCP (например, Kilo):

- **stdio** — команда `.venv/Scripts/v8help-mcp.exe` (или `v8help-mcp` из PATH);
- **HTTP** — `v8help serve --http --host 0.0.0.0 --port 8000`, URL
  `http://<host>:8000/mcp` (см. [Docker](docker.md)).

Инструменты:

### search

`search(query, section?, kind?, limit?)` — поиск (FTS / векторный / гибридный, в
зависимости от `search.backend`); возвращает чанки с метаданными родителя.

- `query` — поисковый запрос (обязательный);
- `section` — фильтр: `objects`/`tables`/`lang`/`query`/`clang`;
- `kind` — фильтр: `page`/`member`/`index`;
- `limit` — максимум результатов.

### get_page

`get_page(id, chunk?, max_chars?)` — текст страницы.

- `id` — строка (одна страница) или массив строк (2–10 статей одним вызовом);
- `chunk` — номер чанка (0-based) для чтения части длинной статьи;
- `max_chars` — лимит суммарного размера ответа (для массива id, по умолчанию 4000).

Длинные статьи (>4000 символов) целиком не выдаются: возвращается список чанков и
первый чанк; конкретный чанк читается через `chunk=N`.

### hierarchy

`hierarchy(section?)` — оглавление: без `section` — сводка по разделам; с `section` —
группы страниц раздела с количеством.

### related

`related(id)` — входящие и исходящие ссылки страницы.

### build

`build(sources?, lang?, cleanup?, force?, chunk_size?, chunk_overlap?)` —
асинхронная пересборка индекса (при заданном эмбеддере — с векторами),
возвращает `job_id` сразу. Параметры:

- `sources` — массив источников (по умолчанию все из конфига);
- `lang` — язык `ru`/`en`;
- `cleanup` — удалить corpus после индексации;
- `force` — пересобрать, даже если индекс актуален;
- `chunk_size` — целевой размер чанка в символах (по умолчанию 1500);
- `chunk_overlap` — перекрытие соседних чанков в символах (по умолчанию 200).

### build_status

`build_status(job_id)` — статус асинхронной сборки (`running` / `done` / `error` +
прогресс).

### discover

`discover()` — показать конфиг и автодискавери: каталог `bin` установленной
платформы 1С (реестр Uninstall/ФС на Windows; `/opt/1cv8`, `/usr/lib`,
`/usr/local` и `PATH` на Linux), доступные эмбеддеры на localhost-портах
(LM Studio/Ollama) и состояние индекса. В контейнере Docker
`platforms`/`embedders` обычно пустые — адрес эмбеддера задаётся через
`embedder.*.base_url` в env/toml и виден в `config` вывода.

### config_get / config_set

`config_get()` — текущие настройки (эффективный конфиг): эмбеддер,
`search.backend`, `bin_dir`, книги и пр.

`config_set(values)` — изменить настройки и сохранить в `v8help.toml` (атомарно).
Ключи (плоские):

- `search.backend` (`fts`|`hybrid`|`vectors`), `search.limit`,
  `search.max_chunks_per_page`;
- `build.cleanup`, `build.chunk_size`, `build.chunk_overlap`;
- `embedder.index`/`embedder.query`.{`model`, `base_url`, `api_key`, `dims`,
  `batch_size`, `embed_chars`, `threads`, `provider`};
- `bin_dir`, `lang`, `books`.

Пример:

```
config_set {"values": {"search.backend": "hybrid"}}
```

Полный справочник ключей — в [Конфигурации](configuration.md).

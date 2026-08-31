# Конфигурация

Все настройки — в TOML-файле `v8help.toml` в корне проекта (путь можно задать
глобальной опцией CLI `--config`). Скопируйте пример и укажите путь к `bin` вашей
платформы:

```bash
copy v8help.example.toml v8help.toml    # Windows
cp v8help.example.toml v8help.toml      # Linux/macOS
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
backend = "fts"     # fts | hybrid | vectors
limit = 10

# Эмбеддер для индексации векторов (опционально, для backend=hybrid/vectors):
# [embedder.index]
# provider = "openai"
# model = "text-embedding-qwen3-embedding-0.6b"
# base_url = "http://localhost:1234/v1"
# api_key = ""
# dims = 1024
# batch_size = 64
# embed_chars = 500
# threads = 2

# Эмбеддер для запросов (если не задан — берётся embedder.index):
# [embedder.query]
# provider = "openai"
# model = "text-embedding-qwen3-embedding-0.6b"
# base_url = "http://localhost:1234/v1"
# api_key = ""
# dims = 1024
```

## Ключи

### Общие

| Ключ          | По умолчанию | Описание                                          |
|---------------|--------------|---------------------------------------------------|
| `bin_dir`     | —            | Каталог `bin` платформы 1С (автоопределение, если не задан) |
| `corpus_dir`  | `data/corpus`| Каталог с md-корпусом                             |
| `db_path`     | `data/v8help.db` | Путь к БД индекса                              |
| `lang`        | `ru`         | Язык справки: `ru`/`en`                            |
| `books`       | `[]`         | Список книг по умолчанию (shorthand, через `bin_dir`) |
| `sources`     | `[]`         | Явные источники (`[[sources]]`, см. ниже)          |

### sources (явные источники)

| Ключ     | Описание                                        |
|----------|-------------------------------------------------|
| `id`     | Идентификатор источника                         |
| `hbk`    | Путь к `.hbk`-файлу                            |
| `prefix` | Префикс имён страниц (например `query__`)       |
| `scheme` | Неймспейс `v8help://<scheme>` (например `SyntaxHelperQueries`) |
| `lang`   | Язык книги: `ru`/`en` (по умолчанию `ru`)       |

### search

| Ключ                  | По умолчанию | Описание                                  |
|-----------------------|--------------|-------------------------------------------|
| `backend`             | `fts`        | `fts` \| `hybrid` \| `vectors`            |
| `limit`               | `10`         | Максимум результатов поиска               |
| `max_chunks_per_page` | `2`          | Не более N чанков одной статьи в топе     |

### embedder.index / embedder.query

| Ключ         | По умолчанию | Описание                                            |
|--------------|--------------|-----------------------------------------------------|
| `provider`   | `""`         | `openai` (OpenAI-совместимый API) \| `hf` (native Hugging Face pipeline) |
| `model`      | `""`         | Идентификатор модели эмбеддинга                      |
| `base_url`   | `""`         | Базовый URL API (`http://host:port/v1`)              |
| `api_key`    | `""`         | Ключ (для облачных API)                              |
| `dims`       | `0`          | Размерность векторов (проверяется при сборке)        |
| `batch_size` | `64`         | Размер батча эмбеддинга                              |
| `embed_chars`| `500`        | Усечение тела при эмбеддинге (символов)              |
| `threads`    | `2`          | Потоков для эмбеддинга при индексации                |

### build

| Ключ           | По умолчанию | Описание                                   |
|----------------|--------------|--------------------------------------------|
| `cleanup`      | `false`      | Удалить corpus после индексации            |
| `chunk_size`   | `1500`       | Целевой размер чанка в символах            |
| `chunk_overlap`| `200`        | Перекрытие соседних чанков в символах      |

## Неймспейсы

Встроенные неймспейсы `v8help://<scheme>` → префикс имён:

| scheme                       | prefix    | книга       |
|------------------------------|-----------|-------------|
| `SyntaxHelperContext`        | (пусто)   | `shcntx_*`  |
| `SyntaxHelperLanguage`       | `lang__`  | `shlang_*`  |
| `SyntaxHelperQueries`        | `query__` | `shquery_*` |
| `SyntaxHelperCommonLanguage` | `clang__` | `shclang_*` |
| `dcsui`                      | `dcsui__` | `dcsui_*` (справочник СКД) |

## Готовые индексы (без установленной платформы)

Если пересобирать индекс не хочется/невозможно, готовые БД публикуются как
архивы в [GitHub Releases](https://github.com/sergeyfedyakov/v8help-mcp/releases) —
по одной на версию платформы. «Актуальная» база лежит в теге `ready-to-run-db`
(готовые индексы) и обновляется независимо от релизов. Скачайте архив, распакуйте
и укажите путь:

```toml
# v8help.toml
db_path = "C:/path/to/v8help.db"
```

Поиск (включая векторный, если в БД есть векторы) работает без установленной
платформы 1С и без эмбеддера на этапе индексации.

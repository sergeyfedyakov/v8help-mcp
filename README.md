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
- Ранжирование FTS с весами полей `title`/`description`/`body` (9/3/1): совпадение
  в заголовке или в секции «Описание» метода весомее совпадения в теле.
- Чанкование длинных статей (настраиваемые `chunk_size`/`chunk_overlap`) с
  метаданными чанка (родитель, соседние чанки) — единицы поиска и чтения.
- Векторный и гибридный поиск (FTS + эмбеддинги, RRF-фьюжн) через
  OpenAI-совместимый API эмбеддингов (LM Studio, Ollama).
- Асинхронная сборка через MCP: `build` возвращает `job_id` сразу, прогресс —
  через `build_status`; поиск при этом не блокируется (атомарная подмена БД).
- Автодискавери: каталог `bin` платформы (реестр Uninstall/ФС) и доступные
  эмбеддеры на localhost-портах; настройка через MCP (`config_get`/`config_set`).

## Требования

- Python 3.11+
- Установленная платформа 1С:Предприятие (каталог `bin` с `.hbk`-файлами) — нужна
  только для пересборки индекса; для поиска достаточно готовой БД (см. «Готовые
  индексы» ниже).
- (опционально) эмбеддер для векторного поиска — LM Studio или Ollama.

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
backend = "fts"     # fts | hybrid | vectors
limit = 10

# Эмбеддер для индексации векторов (опционально, для backend=hybrid/vectors):
# [embedder.index]
# model = "text-embedding-qwen3-embedding-0.6b"
# base_url = "http://localhost:1234/v1"
# dims = 1024
# batch_size = 64

# Эмбеддер для запросов (если не задан — берётся embedder.index):
# [embedder.query]
# model = "text-embedding-qwen3-embedding-0.6b"
# base_url = "http://localhost:1234/v1"
# dims = 1024
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
v8help build --chunk-size 1500 --chunk-overlap 200   # пересобрать с другими параметрами чанков
v8help search "регулярному"        # полнотекстовый поиск
v8help get-page lang__def_String    # текст страницы (короткие — целиком)
v8help get-page lang__def_String --chunk 2   # конкретный чанк длинной статьи
v8help hierarchy                   # сводка по разделам
v8help related lang__def_String    # связанные страницы
v8help serve                       # MCP-сервер (stdio)
```

## MCP

Подключение в клиенте MCP (например, Kilo) — команда `.venv/Scripts/v8help-mcp.exe`
(или `v8help-mcp` из PATH).

Инструменты:

- `search(query, section?, kind?, limit?)` — поиск (FTS / векторный / гибридный,
  в зависимости от `search.backend`); возвращает чанки с метаданными родителя.
- `get_page(id, chunk?, max_chars?)` — текст страницы: `id` — строка или массив
  строк (2–10 статей за вызов, до `max_chars=4000`); длинные статьи целиком не
  выдаются — возвращается список чанков и первый чанк, конкретный читается через
  `chunk=N`.
- `hierarchy(section?)` — оглавление/сводка разделов.
- `related(id)` — входящие и исходящие ссылки страницы.
- `build(sources?, lang?, cleanup?, force?)` — асинхронная пересборка индекса
  (при заданном эмбеддере — с векторами), возвращает `job_id`.
- `build_status(job_id)` — статус сборки (`running` / `done` / `error` + прогресс).
- `discover()` — платформа 1С, эмбеддеры на localhost, состояние индекса.
- `config_get()` / `config_set(values)` — просмотр и правка настроек (эмбеддер,
  `search.backend`, `bin_dir` и пр.) с сохранением в `v8help.toml`.

## Эмбеддинги и гибридный поиск

Векторный поиск нужен для синонимии, которую не ловит FTS (например, запрос
«проверка строки по регулярному выражению» → метод `СтрПодобна`). Требует
OpenAI-совместимого сервиса эмбеддингов.

**Вариант A — LM Studio:** запустите модель
`text-embedding-qwen3-embedding-0.6b` (1024 dims) и пропишите
`[embedder.index]`/`[embedder.query]` с `base_url = "http://localhost:1234/v1"`.

**Вариант B — Ollama одной командой** (рекомендуется в README):

```powershell
.\scripts\setup-ollama.ps1              # установит Ollama, поднимет serve,
                                        # скачает bge-m3 (1024 dims) и покажет конфиг
```

Скрипт выведет готовую секцию `[embedder.index]`/`[embedder.query]` для
`v8help.toml` (или значения для MCP-тула `config_set`). После этого:

```bash
v8help build          # пересобрать индекс с векторами (несколько минут)
```

и в `v8help.toml` переключите `[search] backend = "hybrid"` (либо
`config_set` → `{"search.backend": "hybrid"}`).

**Вариант C — Hugging Face Inference (облако, без своего сервера):**

1. Зарегистрируйтесь на [huggingface.co](https://huggingface.co) и создайте токен:
   Settings → Access Tokens (права «Read» достаточно).
2. Вставьте токен в `api_key` секции `[embedder.query]` (для работы с готовым
   индексом этого достаточно; для полной индексации через облако — продублируйте
   в `[embedder.index]`):

```toml
[embedder.query]
provider = "hf"                          # native HF pipeline, не OpenAI-формат
model = "unsloth/Qwen3-Embedding-0.6B"   # та же модель, что построила индекс
base_url = "https://router.huggingface.co/hf-inference/models"
api_key = "<HF_TOKEN>"
dims = 1024
```

3. Готово: поиск (гибрид/векторы) работает по готовому индексу, локальный
   эмбеддер не нужен. Тариф модели — ≈$0.01 за 1 млн токенов; полная индексация
   корпуса ≈ 12–25 млн токенов ≈ **$0.12–0.25** (бесплатный грант $0.10/мес не
   покрывает полный индекс, но на запросы хватает с большим запасом).

Модель обязана совпадать с моделью индекса (векторное пространство должно быть
одно). Для локальной `text-embedding-qwen3-embedding-0.6b` (LM Studio) и облачной
`unsloth/Qwen3-Embedding-0.6B` совместимость проверена: косинус ≈ 0.9995.

### Зачем разделять `embedder.index` и `embedder.query`

Обе секции описывают **одну и ту же модель**: эмбеддинги запроса и индекса должны
лежать в одном векторном пространстве, иначе косинусная близость бессмысленна.
Разделение нужно по жизненному циклу:

- `embedder.index` — модель для сборки индекса (разовая фоновая операция, долго);
- `embedder.query` — модель для эмбеддинга текста запроса при каждом поиске
  (реальное время). Если не задана — берётся `embedder.index`.

Практический сценарий — **готовый индекс + облачная модель**: скачали БД с
векторами (см. «Готовые индексы» ниже) — эмбеддинги уже посчитаны, собственный
сервер эмбеддинга для индексации не нужен. Тогда в `embedder.query` указывается
**та же модель**, но доступная через недорогой облачный API (вариант C выше,
`provider = "hf"`) — вместо того чтобы поднимать локальный сервер на целевой
машине. `provider` работает в обеих секциях: если лимиты/тариф позволяют,
`[embedder.index]` тоже можно указать `provider = "hf"` и строить индекс целиком
через облако.

## Готовые индексы (без установленной платформы)

Если пересобирать индекс не хочется/невозможно, готовые БД публикуются как
архивы в [GitHub Releases](https://github.com/sergeyfedyakov/v8help-mcp/releases) —
по одной на версию платформы. Скачайте архив, распакуйте и укажите путь:

```toml
# v8help.toml
db_path = "C:/path/to/v8help.db"
```

Поиск (включая векторный, если в БД есть векторы) работает без установленной
платформы 1С и без эмбеддера на этапе индексации.

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

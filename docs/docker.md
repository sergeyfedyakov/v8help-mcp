# Запуск в Docker

Один контейнер без docker-compose. Сервис поднимает streamable-http MCP-сервер
на порту 8000 (путь `/mcp`), БД держит в каталоге `/data`. Эмбеддер — внешний
(адрес задаётся env/toml, в образе нет Ollama/LM Studio).

## ⚠️ ВАЖНО: скачивание базы при первом запуске

Если в `/data` нет файла `v8help.db`, `entrypoint.sh` **по умолчанию скачает
готовый индекс** (~167 МБ, архив `v8help.db.zip` из GitHub Releases, тег
`ready-to-run-db` — «готовые индексы»: актуальная база, обновляется независимо от
релизов) и распакует его. Первый запуск поэтому занимает время и трафик.

- Отключить авто-скачивание: `-e V8HELP_INIT_DB=false` (сервер стартует без БД —
  поиск вернёт пусто, пока не появится `v8help.db`);
- Указать другой архив: `-e V8HELP_RELEASE_URL=…`;
- Проверять целостность: `-e V8HELP_RELEASE_SHA256=<sha256>` (при наличии);
- Или просто положить готовый `v8help.db` в каталог `/data` заранее — скачивания
  не будет.

## Сборка образа

```bash
docker build -t v8help:0.10.0 .
```

`Makefile` (Linux): `make build`, `make run`, `make test`, `make stop`,
`make logs`.

## Минимальный запуск

```bash
mkdir -p ./data && sudo chown 1000:1000 ./data   # Linux: права на запись для контейнера
docker run -d --name v8help \
    -p 8000:8000 \
    -v ./data:/data \
    v8help:0.10.0
```

Только это и нужно для работы: порт 8000 и каталог с БД. При первом старте в
`./data` скачается готовый индекс (см. предупреждение выше) — сервер поднимется,
когда загрузка завершится.

- `-v ./data:/data` — bind-mount локального каталога вместо именованного volume:
  БД лежит рядом с вашим проектом и видна напрямую.
- Права: контейнер работает от non-root (uid 1000), поэтому каталог должен быть
  доступен ему на запись. На Linux — `chown 1000:1000` (см. выше); на Docker
  Desktop (Windows/macOS) файловая система хоста доступна на запись без
  дополнительных шагов.
- Если вместо bind-mount удобнее именованный volume — `-v v8help-data:/data`
  (создастся автоматически, права настроятся сами).

Проверка:

```bash
curl -s http://localhost:8000/mcp   # любой HTTP-ответ = сервер жив
docker ps                            # healthcheck → healthy
```

## Расширенный запуск (с эмбеддером)

Векторный/гибридный поиск требует эмбеддер для запросов. Он живёт на хосте —
контейнер обращается к нему по `host.docker.internal` (или IP-адресу машины):

```bash
docker run -d --name v8help \
    -p 8000:8000 \
    -v ./data:/data \
    --add-host host.docker.internal:host-gateway \
    -e V8HELP_EMBEDDER_QUERY_BASE_URL=http://host.docker.internal:11434/v1 \
    -e V8HELP_SEARCH_BACKEND=hybrid \
    v8help:0.10.0
```

- `--add-host host.docker.internal:host-gateway` — на Linux-движке даёт доступ к
  хосту по имени `host.docker.internal`. Если эмбеддер на другой машине — укажите
  её IP в `V8HELP_EMBEDDER_QUERY_BASE_URL` (и добавьте `host-gateway`-строку не
  нужно).
- `V8HELP_SEARCH_BACKEND=hybrid` — FTS + векторы (или `vectors` — только
  векторы). Требует векторы в БД (в готовом индексе из Releases они есть) и
  доступный эмбеддер.
- `V8HELP_EMBEDDER_INDEX_BASE_URL` — адрес эмбеддера для **сборки** индекса
  (если собираете с векторами, см. ниже).

## Самостоятельная сборка индекса в Docker (без эмбеддера)

Если нужен индекс под свою версию платформы — соберите его в контейнере из
`.hbk`-файлов установленной платформы. Для этого нужны два тома и отключённый
инит:

```bash
mkdir -p ./data && sudo chown 1000:1000 ./data
docker run --rm \
    -v ./data:/data \
    -v /opt/1cv8:/opt/1cv8:ro \
    -e V8HELP_INIT_DB=false \
    v8help:0.10.0 v8help build --force
```

- `-e V8HELP_INIT_DB=false` — **ключ инита**: отключаем автозагрузку готовой БД
  (собираем свою, скачивать нечего);
- `-v /opt/1cv8:/opt/1cv8:ro` — **том с бинарниками платформы**: каталог
  установки, где лежат `.hbk`-источники. Автодискавери сам найдёт `bin_dir`
  (Linux-поиск: `/opt/1cv8/**/<версия>[/bin]`, `which 1cv8`);
- Сборка идёт без эмбеддинга (только FTS), если не задан
  `V8HELP_EMBEDDER_INDEX_BASE_URL`/модель. Корпус пишется в `/data/corpus`
  (`-e V8HELP_BUILD_CLEANUP=true` — удалить после индексации).

После сборки запустите сервер с тем же каталогом:

```bash
docker run -d --name v8help -p 8000:8000 -v ./data:/data v8help:0.10.0
```

## Переменные окружения

Полный набор `V8HELP_*` перекрывает конфиг (см. configuration.md). Часто
используемые в Docker:

| Переменная | Описание |
|------------|----------|
| `V8HELP_DB_PATH` | Путь к БД (по умолчанию `/data/v8help.db`) |
| `V8HELP_INIT_DB` | Авто-скачивание БД, если отсутствует (по умолчанию `true`) |
| `V8HELP_RELEASE_URL` | URL архива БД для инициализации (по умолчанию — тег `ready-to-run-db`, «готовые индексы») |
| `V8HELP_RELEASE_SHA256` | Ожидаемый SHA-256 архива (проверка целостности) |
| `V8HELP_EMBEDDER_QUERY_BASE_URL` | Адрес эмбеддера для поиска (например `http://host.docker.internal:11434/v1`) |
| `V8HELP_EMBEDDER_INDEX_BASE_URL` | Адрес эмбеддера для сборки индекса |
| `V8HELP_SEARCH_BACKEND` | `fts` / `hybrid` / `vectors` |

## Тесты в контейнере

```bash
docker run --rm --entrypoint python v8help:0.10.0 -m pytest /app/tests -q
```

## MCP-клиент (Kilo)

URL сервера: `http://<host-ip>:8000/mcp`, тип подключения **HTTP**.

## Особенности контейнера

- **discover**: автодискавери платформ и эмбеддеров внутри контейнера обычно
  пустой — платформа 1С не установлена (Linux), localhost-порты эмбеддеров
  принадлежат контейнеру. Реальный адрес эмбеддера задаётся env и виден в
  `config` вывода `discover`; состояние индекса и конфиг — на месте.
- Контейнер запускается от non-root (uid 1000).

# Проектные инструкции

## Индексация Kilo (семантический поиск)

В этом проекте включено индексирование Kilo (см. `kilo.jsonc` → `indexing.enabled: true`).

Перед операциями, которые зависят от семантического поиска (`semantic_search`) или индексации:

1. Прочитай `kilo.jsonc` и проверь `indexing.enabled` и `indexing.provider`.
2. Провайдер эмбеддинга — LM Studio (OpenAI-совместимый endpoint `http://localhost:1234/v1`), модель `text-embedding-qwen3-embedding-0.6b` (мультиязычная, 1024 измерения, поддерживает русский). Перед операциями индексации убедись, что `http://localhost:1234/v1/models` отвечает (GET запрос).
3. Если не отвечает — запусти `lms server start` как **фоновый процесс** (инструмент background_process). Процесс автоматически остановится при завершении сессии Kilo. После старта повторно проверь `/v1/models`; если нужной embedding-модели нет в списке — загрузи её через `lms load <идентификатор>` и укажи в запросе.
4. Проверь, что сервер отдаёт эмбеддинги размерности 1024 (соответствует `indexing.dimension`).

### Статус "IDX Disabled" (consent в VS Code)

Симптом: конфиг и провайдер настроены верно, но индексация не стартует, статус `Disabled`.

Причина: в VS Code индексация дополнительно управляется **consent** (разрешением на проект), а не только `indexing.enabled`. Пока consent не установлен, статус `Disabled` с сообщением `Codebase indexing is disabled until you enable it for this project in Kilo Settings.`. UI-тумблеры в некоторых версиях расширения (например 7.4.20) consent НЕ выставляют, поэтому они не помогают.

Обходной путь — выставить consent напрямую через внутренний HTTP-API процесса kilo:

1. Найди порт сервера (обычно процесс `kilo.exe` из VS Code): `netstat -ano | findstr LISTENING` и сопоставь по PID.
2. Basic-аутентификация: логин `kilo`, пароль из `$env:KILO_SERVER_PASSWORD`.
3. `PUT http://127.0.0.1:<порт>/indexing/consent?directory=<URL-encoded путь проекта>` с телом `{"enabled":true}` и заголовком `x-kilo-directory: <путь проекта>`.
4. Проверь статус: `GET http://127.0.0.1:<порт>/indexing/status?directory=<путь>` — должен стать `In Progress`/`Complete`.

Важно: consent хранится в памяти текущего процесса `kilo.exe` и НЕ персистится (globalStorage расширения пуст). После перезапуска VS Code индексация снова станет `Disabled` — нужно выставлять consent заново.

Логи индексации и работы Kilo: `~/.local/share/kilo/log/opencode.log` и `dev.log`.

## Кириллица в консоли Windows
PowerShell 5.1 выводит UTF-8 как кракозябры (OEM-кодировка). Это косметика терминала, не дефект кода.

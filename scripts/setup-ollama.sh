#!/usr/bin/env bash
# Быстрый старт v8help с эмбеддингами через Ollama (Linux/macOS).
# Устанавливает Ollama (если нет), поднимает serve, скачивает модель эмбеддингов
# и выводит готовый конфиг для v8help.toml / MCP-тула config_set.
#
# Использование:
#   ./scripts/setup-ollama.sh
#   ./scripts/setup-ollama.sh --model nomic-embed-text --dims 768

set -euo pipefail

MODEL="bge-m3"
DIMS=1024
PORT=11434
BATCH_SIZE=64

usage() {
    sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -m|--model) MODEL="$2"; shift 2 ;;
        -d|--dims) DIMS="$2"; shift 2 ;;
        -p|--port) PORT="$2"; shift 2 ;;
        -b|--batch) BATCH_SIZE="$2"; shift 2 ;;
        -h|--help) usage ;;
        *) echo "Неизвестный аргумент: $1" >&2; usage ;;
    esac
done

BASE_URL="http://localhost:${PORT}/v1"

echo "== v8help: настройка Ollama =="

# 1. Установка Ollama
if ! command -v ollama >/dev/null 2>&1; then
    echo "[1/3] Ollama не найден. Устанавливаю через официальный скрипт..."
    curl -fsSL https://ollama.com/install.sh | sh
else
    echo "[1/3] Ollama найден."
fi

# 2. Запуск сервера
if curl -fsS -m 3 "$BASE_URL/version" >/dev/null 2>&1; then
    echo "[2/3] Сервер Ollama уже работает ($BASE_URL)."
else
    echo "[2/3] Запускаю 'ollama serve'..."
    nohup ollama serve >/tmp/ollama.log 2>&1 &
    sleep 3
fi

# 3. Модель эмбеддингов
echo "[3/3] Скачиваю модель эмбеддингов '$MODEL'..."
ollama pull "$MODEL"

# 4. Вывод рекомендованного конфига
cat <<EOF

Готово. Добавьте в v8help.toml:
-----
[embedder.index]
model = "$MODEL"
base_url = "$BASE_URL"
api_key = ""
dims = $DIMS
batch_size = $BATCH_SIZE

[embedder.query]
model = "$MODEL"
base_url = "$BASE_URL"
api_key = ""
dims = $DIMS
batch_size = $BATCH_SIZE
-----

Либо через MCP-тул config_set:
config_set {"values": {"search.backend": "hybrid", "embedder.index.model": "$MODEL", "embedder.index.base_url": "$BASE_URL", "embedder.index.dims": $DIMS}}

После этого пересоберите индекс: v8help build
EOF

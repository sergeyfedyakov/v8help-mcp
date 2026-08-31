#!/bin/sh
set -eu

DB_PATH="${V8HELP_DB_PATH:-/data/v8help.db}"
INIT="${V8HELP_INIT_DB:-true}"
RELEASE_URL="${V8HELP_RELEASE_URL:-https://github.com/sergeyfedyakov/v8help-mcp/releases/download/ready-to-run-db/v8help.db.zip}"
SHA256="${V8HELP_RELEASE_SHA256:-}"

if [ "$INIT" = "true" ] && [ ! -f "$DB_PATH" ]; then
    echo "[v8help] $DB_PATH РЅРµ РЅР°Р№РґРµРЅ вЂ” СЃРєР°С‡РёРІР°СЋ РіРѕС‚РѕРІС‹Р№ РёРЅРґРµРєСЃ ($RELEASE_URL)"
    mkdir -p "$(dirname "$DB_PATH")"
    TMPZIP="$(dirname "$DB_PATH")/v8help.db.zip"
    python - "$RELEASE_URL" "$TMPZIP" <<'PY'
import sys, urllib.request
url, dst = sys.argv[1], sys.argv[2]
with urllib.request.urlopen(url, timeout=300) as r, open(dst, "wb") as f:
    f.write(r.read())
PY
    if [ -n "$SHA256" ]; then
        echo "$SHA256  $TMPZIP" | sha256sum -c - || {
            echo "[v8help] РєРѕРЅС‚СЂРѕР»СЊРЅР°СЏ СЃСѓРјРјР° РЅРµ СЃРѕРІРїР°Р»Р°" >&2
            rm -f "$TMPZIP"
            exit 1
        }
    fi
    TMPDIR="$(mktemp -d)"
    unzip -q "$TMPZIP" -d "$TMPDIR"
    FOUND="$(find "$TMPDIR" -name v8help.db -type f | head -n 1)"
    if [ -z "$FOUND" ]; then
        echo "[v8help] РІ Р°СЂС…РёРІРµ РЅРµ РЅР°Р№РґРµРЅ v8help.db" >&2
        rm -rf "$TMPDIR" "$TMPZIP"
        exit 1
    fi
    mv "$FOUND" "$DB_PATH"
    rm -rf "$TMPDIR" "$TMPZIP"
    echo "[v8help] РёРЅРґРµРєСЃ РіРѕС‚РѕРІ: $DB_PATH"
fi

if [ ! -f "$DB_PATH" ]; then
    echo "[v8help] Р’РќРРњРђРќРР•: $DB_PATH РѕС‚СЃСѓС‚СЃС‚РІСѓРµС‚ вЂ” РїРѕРёСЃРє РІРµСЂРЅС‘С‚ РїСѓСЃС‚Рѕ. РђРІС‚Рѕ-СЃРєР°С‡РёРІР°РЅРёРµ: V8HELP_INIT_DB=true (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ)" >&2
fi

exec "$@"

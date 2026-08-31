"""Liveness-проверка контейнера: любой HTTP-ответ от /mcp означает «сервер жив»."""
import sys
import urllib.error
import urllib.request

try:
    urllib.request.urlopen("http://127.0.0.1:8000/mcp", timeout=3)
except urllib.error.HTTPError:
    pass
except Exception:
    sys.exit(1)

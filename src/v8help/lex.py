"""Лексическая нормализация: сплит склеенных PascalCase-идентификаторов.

Шаг 1 качества поиска: 1С склеивает многословные имена в один идентификатор
(`ПолучитьДатуНачала`, `СтрНайтиПоРегулярномуВыражению`). Разбиваем по границам
регистра (работает и для кириллицы), сохраняя при этом исходную форму.
"""

from __future__ import annotations

import re

_WORD_RE = re.compile(r"[A-Za-zА-ЯЁа-яё0-9]+")


def split_pascal(word: str) -> list[str]:
    """Разбивает PascalCase-идентификатор на части. Без границ — возвращает как есть."""
    if not word:
        return []
    parts: list[str] = []
    start = 0
    n = len(word)
    for i in range(1, n):
        c = word[i]
        p = word[i - 1]
        if c.isupper() and (p.islower() or p.isdigit()):
            parts.append(word[start:i])
            start = i
        elif c.islower() and p.isupper() and i > start + 1:
            parts.append(word[start:i - 1])
            start = i - 1
    parts.append(word[start:])
    return parts


def expand(text: str) -> str:
    """Возвращает исходный текст + строки с расщеплёнными идентификаторами.

    Исходная форма сохраняется (для точного поиска по полному имени),
    расщеплённые части добавляются отдельными токенами (для поиска по частям).
    """
    out = [text]
    for m in _WORD_RE.finditer(text):
        w = m.group(0)
        parts = split_pascal(w)
        if len(parts) > 1:
            out.append(" ".join(parts))
    return "\n".join(out)

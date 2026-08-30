"""Разбиение длинных статей на чанки по границам строк.

Целевой размер чанка ~``chunk_size`` символов, между соседними чанками —
перекрытие ``overlap`` символов (для сохранения контекста). Разрез всегда
проходит по границе строки: короткие строки копятся накопительно, пока не
наберётся ~``chunk_size``. Одна строка длиннее ``chunk_size`` (редкие таблицы/
блоки) режется посимвольно на отдельные чанки с шагом ``chunk_size - overlap``.
"""

from __future__ import annotations


def chunk_text(
    text: str,
    chunk_size: int = 1500,
    overlap: int = 200,
) -> list[str]:
    """Делит ``text`` на чанки. Короткий текст возвращается как один чанк."""
    if len(text) <= chunk_size:
        return [text]

    lines = text.split("\n")
    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0
    dirty = False  # buf содержит строки, ещё не выписанные в chunks

    def flush() -> None:
        """Пишет буфер в chunks, оставляет хвост ~overlap в buf как перекрытие."""
        nonlocal buf, buf_len, dirty
        if not buf:
            return
        chunks.append("\n".join(buf))
        tail: list[str] = []
        tl = 0
        for ln in reversed(buf):
            if tail and tl + len(ln) + 1 > overlap:
                break
            if not tail and overlap <= 0:
                break
            tail.insert(0, ln)
            tl += len(ln) + 1
        buf = tail
        buf_len = tl
        dirty = False

    for ln in lines:
        if len(ln) > chunk_size:
            flush()
            step = max(chunk_size - overlap, 1)
            # Окна с конца, чтобы последний кусок не оказался крошечным.
            parts: list[str] = []
            start = len(ln) - chunk_size
            while start > 0:
                parts.append(ln[start : start + chunk_size])
                start -= step
            parts.append(ln[: start + chunk_size])
            parts.reverse()
            chunks.extend(parts)
            buf = []
            buf_len = 0
            dirty = False
            continue
        if buf and buf_len + len(ln) + 1 > chunk_size:
            flush()
        buf.append(ln)
        buf_len += len(ln) + 1
        dirty = True

    if dirty and buf:
        chunks.append("\n".join(buf))
    return chunks

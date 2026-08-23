"""Чтение V8-контейнеров справки (.hbk).

Обходит баг ``onec_dtools.container_reader.read_entries`` (не умеет 4-байтные
свободные блоки в TOC), используя парсинг TOC по длине куска.
"""

from __future__ import annotations

import io
import struct
import zipfile
from pathlib import Path

from onec_dtools.container_reader import (
    END_MARKER,
    read_document_gen,
    read_full_document,
)

FORMAT15 = 15
FORMAT16 = 16

_FORMAT16_SENTINEL = b"\xff" * 8
_TOC_OFFSET = struct.calcsize("4i")
_DESC_BASE = struct.calcsize("QQi")


def detect_format(file) -> int:
    """Format15 (заголовок 16 байт '4i') vs Format16 (sentinel FF*8)."""
    file.seek(0)
    head = file.read(8)
    file.seek(0)
    return FORMAT16 if head == _FORMAT16_SENTINEL else FORMAT15


def parse_toc(data: bytes) -> list[tuple[int, int]]:
    """Список (desc_off, data_off). 4-байтные куски — свободные блоки, 0 — пустые."""
    parts = data.split(struct.pack("i", END_MARKER))
    files: list[tuple[int, int]] = []
    for part in parts[:-1]:
        if len(part) == 8:
            files.append(struct.unpack("2i", part))
    return files


def read_entries_fixed(file) -> dict[str, int]:
    """Имя файла -> смещение данных. Корректно обрабатывает свободные блоки TOC."""
    file.seek(0)
    struct.unpack("4i", file.read(_TOC_OFFSET))
    toc = read_full_document(file, _TOC_OFFSET)
    result: dict[str, int] = {}
    for desc_off, data_off in parse_toc(toc.data):
        desc_doc = read_full_document(file, desc_off)
        fmt = "".join(["QQi", str(desc_doc.size - _DESC_BASE), "s"])
        desc = struct.unpack(fmt, desc_doc.data)
        name = desc[3].decode("utf-16").partition("\x00")[0]
        result[name] = data_off
    return result


def read_data(file, data_off: int) -> bytes:
    gen = read_document_gen(file, data_off)
    next(gen)
    return b"".join(gen)


class HbkContainer:
    """Контейнер справки 1С: автоопределение формата + доступ к файлам по имени."""

    def __init__(self, file) -> None:
        self.file = file
        self.format = detect_format(file)

    @classmethod
    def open(cls, path: Path) -> "HbkContainer":
        return cls(path.open("rb"))

    @property
    def entries(self) -> dict[str, int]:
        if self.format == FORMAT16:
            raise NotImplementedError(
                "Format16 (новый .hbk) пока не поддерживается — в 8.5 его нет"
            )
        return read_entries_fixed(self.file)

    def read_file(self, name: str) -> bytes:
        entries = self.entries
        if name not in entries:
            raise KeyError(f"В контейнере нет файла: {name}")
        return read_data(self.file, entries[name])

    def file_storage(self) -> zipfile.ZipFile:
        """FileStorage (ZIP с HTML-страницами) как открытый архив."""
        return zipfile.ZipFile(
            io.BytesIO(self.read_file("FileStorage")),
            mode="r",
            metadata_encoding="utf-8",
        )

import io
import struct

from v8help.unpack.container import detect_format, parse_toc

END_MARKER = 2147483647


def _marker():
    return struct.pack("i", END_MARKER)


def test_parse_toc_handles_free_blocks_and_empty():
    entry = struct.pack("2i", 100, 200)
    free = struct.pack("i", 300)
    data = entry + _marker() + free + _marker() + b"" + _marker()
    assert parse_toc(data) == [(100, 200)]


def test_parse_toc_empty():
    assert parse_toc(b"") == []


def test_detect_format15():
    assert detect_format(io.BytesIO(b"\x00" * 16)) == 15


def test_detect_format16():
    assert detect_format(io.BytesIO(b"\xff" * 8)) == 16

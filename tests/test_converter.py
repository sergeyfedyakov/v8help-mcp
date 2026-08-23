from bs4 import BeautifulSoup

from v8help.converter import (
    archive_path_to_filename,
    build_archive_index,
    quick_extract_title,
    rewrite_links,
    title_to_filename,
)


def test_archive_path_to_filename():
    assert archive_path_to_filename("ACos") == "ACos.md"
    assert archive_path_to_filename("objects/x/y.html") == "objects__x__y.md"
    assert archive_path_to_filename("ACos", prefix="query__") == "query__ACos.md"
    assert archive_path_to_filename("builtin_functions.html", prefix="query__") == "query__builtin_functions.md"


def test_title_to_filename():
    assert title_to_filename("Функция ACos") == "Функция_ACos.md"
    assert title_to_filename("A/B:c*d") == "ABcd.md"


def test_quick_extract_title(tmp_path):
    p = tmp_path / "a.html"
    p.write_bytes('<h1 class="V8SH_pagetitle">Строка (String)</h1>'.encode("utf-8"))
    assert quick_extract_title(p) == "Строка (String)"


def test_build_archive_index_title_based(tmp_path):
    (tmp_path / "a.html").write_bytes(b"<html><body>x</body></html>")
    idx = build_archive_index(tmp_path, "", {"a.html": "Строка"})
    assert idx["a.html"] == "Строка.md"


def test_build_archive_index_path_fallback(tmp_path):
    (tmp_path / "ACos").write_bytes(b"<html><body>x</body></html>")
    idx = build_archive_index(tmp_path, "query__", {"acos": ""})
    assert idx["acos"] == "query__ACos.md"
    assert idx["acos"] == idx.get("acos")


def test_rewrite_links_v8help():
    soup = BeautifulSoup('<a href="v8help://SyntaxHelperQueries/LitHum">Число</a>', "lxml")
    index = {"lithum": "query__LitHum.md"}
    unresolved = []
    rewrite_links(soup, index, unresolved, "ACos", {})
    assert soup.find("a")["href"] == "query__LitHum.md"
    assert unresolved == []

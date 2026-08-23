from v8help import metadata


def test_detect_section():
    assert metadata.detect_section("lang__def_String.md") == "lang"
    assert metadata.detect_section("query__TRUE.md") == "query"
    assert metadata.detect_section("clang__hints.md") == "clang"
    assert metadata.detect_section("tables__x.md") == "tables"
    assert metadata.detect_section("Настройка.md") == "objects"


def test_detect_source():
    assert metadata.detect_source("lang__def_String.md") == "shlang_ru"
    assert metadata.detect_source("query__TRUE.md") == "shquery_ru"
    assert metadata.detect_source("clang__hints.md") == "shclang_ru"
    assert metadata.detect_source("Настройка.md") == "shcntx_ru"


def test_detect_kind():
    assert metadata.detect_kind("Automation_сервер.Connect.md") == "member"
    assert metadata.detect_kind("Настройка.md") == "page"
    assert metadata.detect_kind("_index__Объекты.md") == "index"


def test_extract_title():
    text = "# Automation сервер.Connect\n\nbody"
    assert metadata.extract_title(text, "x.md") == "Automation сервер.Connect"
    assert metadata.extract_title("нет заголовка", "Имя.md") == "Имя"


def test_normalize_target():
    assert metadata.normalize_target("lang__def_String.md") == "lang__def_String"
    assert metadata.normalize_target("v8help://SyntaxHelperQueries/TRUE") == "query__TRUE"
    assert metadata.normalize_target("v8help://SyntaxHelperQueries/query_totals.html") == "query__query_totals"
    assert metadata.normalize_target("v8help://SyntaxHelperLanguage/def_String") == "lang__def_String"
    assert metadata.normalize_target("v8help://SyntaxHelperContext/x") == "x"
    assert metadata.normalize_target("v8help://SyntaxHelperCommonLanguage/hints#frag") == "clang__hints"
    assert metadata.normalize_target("Automation_сервер.md") == "Automation_сервер"
    assert metadata.normalize_target("page.md#anchor") == "page"
    assert metadata.normalize_target("obsidian://open?file=_index.md") is None
    assert metadata.normalize_target("https://example.com") is None


def test_parse_links():
    body = "см. [Строка](lang__def_String.md) и [ИСТИНА](v8help://SyntaxHelperQueries/TRUE)"
    assert metadata.parse_links(body) == ["lang__def_String", "query__TRUE"]

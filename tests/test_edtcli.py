from pathlib import Path

from v8help import edtcli, metadata


def test_list_commands():
    text = (
        "Доступные команды 1С:EDT CLI:\n\nbuild\nclean-up-source\nexport\n\n"
        "Чтобы получить справку по конкретной команде...\n"
    )
    assert edtcli._list_commands(text) == ["build", "clean-up-source", "export"]


def test_variants_numbered_and_usage():
    numbered = 'Варианты вызова:\n1. export --project "s"\n2. export --project-name "s"\n'
    assert edtcli._variants(numbered) == [
        'export --project "s"', 'export --project-name "s"'
    ]
    used = 'Конвертирует CF.\n\nИспользование:\n\n  cf2lib "a" "b"\n\nЕщё текст.\n'
    assert edtcli._variants(used) == ['cf2lib "a" "b"']


def test_summary_export_and_usage():
    export = (
        'Варианты вызова:\n1. export --project "s"\n\nВариант 1:\n'
        'export --project "s"\n    Экспортирует проект.\n'
    )
    assert edtcli._summary(export, "export") == "Экспортирует проект."
    used = "Конвертирует файл формата CF в библиотеку 1C:EDT.\n\nИспользование:\n\n  x\n"
    assert edtcli._summary(used, "x") == "Конвертирует файл формата CF в библиотеку 1C:EDT."


def test_decode_utf8_and_utf16():
    assert edtcli._decode("привет".encode()) == "привет"
    assert edtcli._decode("ok".encode("utf-16-le")) == "ok"
    assert edtcli._decode("привет".encode("utf-16")) == "привет"


def test_render_command_has_links_and_variants():
    body = edtcli._render_command("export", 'Варианты вызова:\n1. export --project "s"\n')
    assert "# Команда `export` (1C:EDT CLI)" in body
    assert "## Варианты вызова" in body
    assert "(edtcli__index)" in body
    assert "(edtcli__status-codes)" in body


def test_render_index_links_commands():
    h = edtcli.EdtHelp(
        commands=["build", "export"],
        helps={"build": "build x\n    Собирает проект.\n", "export": ""},
    )
    idx = edtcli._render_index(h)
    assert "(edtcli__build)" in idx
    assert "(edtcli__export)" in idx
    assert "Собирает проект." in idx


def test_status_bullets():
    text = "Общие коды состояний:\n\n0\n    Норма.\n\n200\n    Ошибка.\n"
    assert edtcli._status_bullets(text) == ["- **0** — Норма.", "- **200** — Ошибка."]


def _fake_help():
    return edtcli.EdtHelp(
        version="2026.1.3.25",
        commands=["export"],
        helps={"export": "Варианты вызова:\n1. export\n"},
        modes="1C:EDT\n",
        status_codes="0\n    Норма.\n",
    )


def test_generate_writes_files(tmp_path, monkeypatch):
    monkeypatch.setattr(edtcli, "collect", lambda cli, lang="ru", emit=None: _fake_help())

    class Cfg:
        edt_docs = True
        lang = "ru"
        corpus_dir = tmp_path

        def resolve_edt_cli(self):
            return Path("fake")

    files = edtcli.generate(Cfg(), emit=None)
    assert sorted(p.name for p in files) == [
        "edtcli__export.md",
        "edtcli__index.md",
        "edtcli__modes.md",
        "edtcli__status-codes.md",
    ]
    body = (tmp_path / "edtcli__export.md").read_text(encoding="utf-8")
    assert body.startswith("# Команда `export`")


def test_generate_disabled_or_missing_cli(tmp_path):
    class Cfg:
        edt_docs = False
        lang = "ru"
        corpus_dir = tmp_path

        def resolve_edt_cli(self):
            return Path("fake")

    assert edtcli.generate(Cfg()) == []

    class Cfg2(Cfg):
        edt_docs = True

        def resolve_edt_cli(self):
            return None

    assert edtcli.generate(Cfg2()) == []


def test_metadata_edtcli_mapping():
    assert metadata.detect_section("edtcli__export.md") == "edt"
    assert metadata.detect_source("edtcli__export.md") == "edtcli"
    assert metadata.detect_kind("edtcli__export.md") == "page"
    assert metadata.parse_links("[x](edtcli__export)") == ["edtcli__export"]

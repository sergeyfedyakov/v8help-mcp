from v8help.cli import build_parser


def test_parser_search():
    args = build_parser().parse_args(["search", "запрос"])
    assert args.command == "search"
    assert args.query == "запрос"


def test_parser_build():
    args = build_parser().parse_args(["build", "--sources", "shcntx_ru", "shlang_ru"])
    assert args.command == "build"
    assert args.sources == ["shcntx_ru", "shlang_ru"]

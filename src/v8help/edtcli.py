"""Справка по командной строке 1C:EDT → markdown-статьи корпуса v8help.

Источник — только вывод ``1cedtcli`` (jars не разбираются): общий ``-help``
(режимы запуска), ``-command help`` (список команд), ``-command help <cmd>`` по
каждой команде (все «Варианты вызова») и ``help --status-codes``.

Статьи пишутся с префиксом ``edtcli__``: обзор ``edtcli__index``, режимы
``edtcli__modes``, коды возврата ``edtcli__status-codes`` и статья на команду.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

PREFIX = "edtcli__"
INDEX_STEM = PREFIX + "index"
MODES_STEM = PREFIX + "modes"
STATUS_STEM = PREFIX + "status-codes"
DEFAULT_TIMEOUT = 300

ProgressFn = Callable[[str, str], None]

_CMD_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_NUM_RE = re.compile(r"^\s*\d+\.\s+(\S.*)$")


@dataclass
class EdtHelp:
    version: str = ""
    modes: str = ""
    commands: list[str] = field(default_factory=list)
    helps: dict[str, str] = field(default_factory=dict)
    status_codes: str = ""


def _decode(data: bytes) -> str:
    """stdout CLI: UTF-8 (команды) либо UTF-16LE (нативная ``-help``)."""
    if not data:
        return ""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    if data.count(b"\x00") > len(data) // 4:
        return data.decode("utf-16-le", errors="replace")
    return data.decode("utf-8", errors="replace")


def _run(cli: Path, args: list[str], timeout: int = DEFAULT_TIMEOUT) -> tuple[int, str]:
    """Запуск ``1cedtcli`` с аргументами; (код, stdout-или-stderr)."""
    try:
        proc = subprocess.run(
            [str(cli), *args], capture_output=True, timeout=timeout
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, f"{type(exc).__name__}: {exc}"
    out = _decode(proc.stdout)
    return proc.returncode, out if out.strip() else _decode(proc.stderr)


def _list_commands(text: str) -> list[str]:
    """Имена команд из вывода ``-command help`` (строки вида lower-case-slug)."""
    return [ln.strip() for ln in text.splitlines() if _CMD_RE.match(ln.strip())]


def _first_line(text: str) -> str:
    for ln in text.splitlines():
        if ln.strip():
            return ln.strip()
    return ""


_SKIP_HEADS = ("Вариант", "Аргументы", "Использование", "Опции", "Пример", "Варианты")
_NUMDOT_RE = re.compile(r"^\d+\.")


def _summary(text: str, cmd: str = "") -> str:
    """Первая содержательная строка описания команды (для обзора)."""
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith(_SKIP_HEADS) or _NUMDOT_RE.match(s):
            continue
        if cmd and (s == cmd or s.startswith(cmd + " ")):
            continue
        return s
    return ""


def _usage_lines(text: str) -> list[str]:
    """Строки после заголовка «Использование:» (раскладка без «Вариантов»)."""
    lines = text.splitlines()
    out: list[str] = []
    for i, ln in enumerate(lines):
        if ln.strip().rstrip(":") != "Использование":
            continue
        for nxt in lines[i + 1:]:
            s = nxt.strip()
            if not s:
                if out:
                    break
                continue
            if nxt.startswith((" ", "\t")):
                out.append(s)
            else:
                break
    return out


def _variants(text: str) -> list[str]:
    """Варианты запуска: нумерованный список либо строки «Использование:»."""
    out = [m.group(1).strip() for ln in text.splitlines() if (m := _NUM_RE.match(ln))]
    return out or _usage_lines(text)


def _nav() -> str:
    return (
        "Навигация: [Все команды](edtcli__index) · "
        "[Режимы запуска](edtcli__modes) · "
        "[Коды возврата](edtcli__status-codes)"
    )


def _render_command(cmd: str, text: str) -> str:
    out = [f"# Команда `{cmd}` (1C:EDT CLI)", "", _nav(), ""]
    variants = _variants(text)
    if variants:
        out += ["## Варианты вызова", ""]
        out += [f"{i}. `{u}`" for i, u in enumerate(variants, 1)]
        out += [""]
    out += ["## Справка", "", "```text", text.rstrip(), "```", ""]
    return "\n".join(out)


def _render_index(h: EdtHelp) -> str:
    ver = f" версии {h.version}" if h.version else ""
    out = [
        "# Командная строка 1C:EDT",
        "",
        f"Справка CLI `1cedtcli`{ver}: все режимы запуска и команды.",
        "",
        f"- [Режимы запуска 1C:EDT CLI]({MODES_STEM})",
        f"- [Коды возврата 1C:EDT CLI]({STATUS_STEM})",
        "",
        f"## Команды ({len(h.commands)})",
        "",
    ]
    for cmd in h.commands:
        s = _summary(h.helps.get(cmd, ""), cmd)
        out.append(f"- [`{cmd}`]({PREFIX}{cmd})" + (f" — {s}" if s else ""))
    return "\n".join(out) + "\n"


def _render_modes(h: EdtHelp) -> str:
    return (
        "# Режимы запуска 1C:EDT CLI\n\n"
        + _nav()
        + "\n\nОбщие параметры командной строки `1cedtcli`.\n\n"
        + "## Справка\n\n```text\n"
        + h.modes.rstrip()
        + "\n```\n"
    )


def _status_bullets(text: str) -> list[str]:
    """Коды возврата: непустые строки без отступа → ``- **код** — описание``."""
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        code = line.strip()
        if code and not line.startswith((" ", "\t")) and not code.startswith("Общие"):
            desc: list[str] = []
            j = i + 1
            while j < len(lines) and lines[j].strip() and lines[j].startswith((" ", "\t")):
                desc.append(lines[j].strip())
                j += 1
            out.append(f"- **{code}** — {' '.join(desc)}" if desc else f"- **{code}**")
            i = j
            continue
        i += 1
    return out


def _render_status(h: EdtHelp) -> str:
    out = ["# Коды возврата 1C:EDT CLI", "", _nav(), "", "## Общие коды", ""]
    out += _status_bullets(h.status_codes)
    out += ["", "## Исходный текст", "", "```text", h.status_codes.rstrip(), "```", ""]
    return "\n".join(out)


def _write_docs(h: EdtHelp, out: Path) -> list[Path]:
    docs = {
        INDEX_STEM: _render_index(h),
        MODES_STEM: _render_modes(h),
        STATUS_STEM: _render_status(h),
    }
    for cmd in h.commands:
        docs[f"{PREFIX}{cmd}"] = _render_command(cmd, h.helps.get(cmd, ""))
    files: list[Path] = []
    for stem, body in docs.items():
        path = out / f"{stem}.md"
        path.write_text(body, encoding="utf-8")
        files.append(path)
    return files


def _collect_help(cli, base, commands, timeout, log) -> dict[str, str]:
    helps: dict[str, str] = {}
    for i, cmd in enumerate(commands, 1):
        log("edtcli", f"help {cmd} ({i}/{len(commands)})")
        helps[cmd] = _run(cli, base + ["help", cmd], timeout)[1]
    return helps


def collect(
    cli: Path, lang: str = "ru", timeout: int = DEFAULT_TIMEOUT,
    emit: ProgressFn | None = None,
) -> EdtHelp:
    """Снять справку CLI: режимы, список команд, help по каждой и коды возврата."""
    log = emit or (lambda s, m: None)
    ws = tempfile.mkdtemp(prefix="v8help-edt-")
    try:
        base = ["-data", ws, "-nl", lang, "-command"]
        log("edtcli", f"1cedtcli: {cli}")
        version = _first_line(_run(cli, base + ["version"], timeout)[1])
        commands = _list_commands(_run(cli, base + ["help"], timeout)[1])
        log("edtcli", f"Команд: {len(commands)}")
        status = _run(cli, base + ["help", "--status-codes"], timeout)[1]
        helps = _collect_help(cli, base, commands, timeout, log)
    finally:
        shutil.rmtree(ws, ignore_errors=True)
    modes = _run(cli, ["-help"], timeout)[1]
    return EdtHelp(version, modes, commands, helps, status)


def generate(
    config, out_dir=None, cli: Path | None = None, lang: str | None = None,
    emit: ProgressFn | None = None,
) -> list[Path]:
    """Сгенерировать статьи EDT в ``out_dir`` (по умолчанию corpus_dir)."""
    if not getattr(config, "edt_docs", True):
        return []
    cli = cli or config.resolve_edt_cli()
    if cli is None:
        return []
    out = Path(out_dir or config.corpus_dir)
    out.mkdir(parents=True, exist_ok=True)
    h = collect(cli, lang=lang or config.lang, emit=emit)
    return _write_docs(h, out)


def _load(config_arg: str | None):
    from v8help.server import load_config

    config, config_path = load_config(config_arg)
    if config_path:
        base = Path(config_path).resolve().parent
        config.corpus_dir = config.corpus_dir if config.corpus_dir.is_absolute() else base / config.corpus_dir
        config.db_path = config.db_path if config.db_path.is_absolute() else base / config.db_path
        if str(config.edt_cli) not in ("", ".") and not config.edt_cli.is_absolute():
            config.edt_cli = base / config.edt_cli
    return config


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="v8help.edtcli", description="Собрать справку CLI 1C:EDT в корпус"
    )
    ap.add_argument("--config", help="путь к v8help.toml")
    ap.add_argument("--out", help="каталог вывода (по умолчанию corpus_dir)")
    ap.add_argument("--cli", help="путь к 1cedtcli")
    ap.add_argument("--lang", default=None, help="язык CLI (по умолчанию из конфига)")
    args = ap.parse_args(argv)

    config = _load(args.config)
    cli = Path(args.cli) if args.cli else config.resolve_edt_cli()
    if cli is None:
        print("Установка 1C:EDT (1cedtcli) не найдена.", file=sys.stderr)
        return 2
    files = generate(
        config, out_dir=args.out, cli=cli, lang=args.lang,
        emit=lambda s, m: print(f"[{s}] {m}", file=sys.stderr, flush=True),
    )
    print(f"Записано статей: {len(files)} -> {args.out or config.corpus_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Утилиты командной строки платформы 1С:Предприятие 8 → markdown корпуса v8help.

Источник — только вывод самих утилит (``rac``, ``ibcmd``, ``ibsrv``,
``webinst``): общий ``help`` и ``help <режим>`` по каждому режиму.

Статьи пишутся с префиксом ``v8cli__``: обзор ``v8cli__index``, обзор утилиты
``v8cli__<util>`` и статья на режим ``v8cli__<util>-<mode>``.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

PREFIX = "v8cli__"
INDEX_STEM = PREFIX + "index"
DEFAULT_TIMEOUT = 60

ProgressFn = Callable[[str, str], None]

# exec-имя -> (заголовок обзора, аргументы общего help, как звать help режима)
UTILS: dict[str, tuple[str, list[str], list[str] | None]] = {
    "rac": (
        "rac — утилита администрирования кластера серверов 1С:Предприятия",
        ["--help"],
        ["help", "{mode}"],
    ),
    "ibcmd": (
        "ibcmd — управление автономным сервером и информационной базой",
        ["--help"],
        ["help", "{mode}"],
    ),
    "ibsrv": ("ibsrv — автономный сервер 1С:Предприятия", ["--help"], None),
    "webinst": ("webinst — публикация веб-клиента", [], None),
}

_MODE_RE = re.compile(r"^(\S+)\s{2,}(.+?)\s*$")
_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]*$")


@dataclass
class UtilHelp:
    name: str
    help_text: str = ""
    modes: list[tuple[str, str]] = field(default_factory=list)
    mode_help: dict[str, str] = field(default_factory=dict)

    @property
    def title(self) -> str:
        return UTILS[self.name][0]


def _decode(data: bytes) -> str:
    """Вывод утилит Windows: консоль cp866, изредка UTF-8/UTF-16."""
    if not data:
        return ""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    for enc in ("utf-8-sig", "cp866", "cp1251"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _run(exe: Path, args: list[str], timeout: int = DEFAULT_TIMEOUT) -> str:
    try:
        proc = subprocess.run([str(exe), *args], capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"{type(exc).__name__}: {exc}"
    out = _decode(proc.stdout)
    err = _decode(proc.stderr)
    return out if out.strip() else err


def parse_modes(text: str) -> list[tuple[str, str]]:
    """Режимы из секции «Поддерживаемые режимы»: [(имя, описание)]."""
    lines = text.splitlines()
    out: list[tuple[str, str]] = []
    for i, ln in enumerate(lines):
        if "Поддерживаемые режимы" not in ln:
            continue
        for nxt in lines[i + 1:]:
            s = nxt.strip()
            if not s:
                continue
            m = _MODE_RE.match(nxt)
            if not m or not _SLUG_RE.match(m.group(1)):
                break
            if m.group(1) != "help":
                out.append((m.group(1), m.group(2)))
        break
    return out


def find_bin(explicit: Path | None = None) -> Path | None:
    """bin платформы с утилитами: явный путь либо свежайшая установка с ibcmd."""
    if explicit is not None:
        return explicit
    from v8help.config import discover_platforms

    for p in discover_platforms():
        bd = Path(p["bin_dir"])
        if (bd / "ibcmd.exe").exists() or (bd / "rac.exe").exists():
            return bd
    return None


def collect(bin_dir: Path, emit: ProgressFn | None = None) -> list[UtilHelp]:
    """Снять справку утилит: общий help + help по каждому режиму."""
    log = emit or (lambda s, m: None)
    result: list[UtilHelp] = []
    for name, (_title, help_args, mode_args) in UTILS.items():
        exe = bin_dir / f"{name}.exe"
        if not exe.exists():
            log(name, f"не найдена: {exe}")
            continue
        h = UtilHelp(name=name, help_text=_run(exe, help_args))
        h.modes = parse_modes(h.help_text)
        log(name, f"help получен, режимов: {len(h.modes)}")
        if mode_args:
            for i, (mode, _desc) in enumerate(h.modes, 1):
                log(name, f"help {mode} ({i}/{len(h.modes)})")
                h.mode_help[mode] = _run(exe, [a.format(mode=mode) for a in mode_args])
        result.append(h)
    return result


def _nav(util: str = "") -> str:
    parts = ["[Утилиты командной строки 1С](%s)" % INDEX_STEM]
    if util:
        parts.append("[%s](%s%s)" % (util, PREFIX, util))
    return "Навигация: " + " · ".join(parts)


def _fence(text: str) -> list[str]:
    return ["```text", (text or "(справка недоступна)").rstrip(), "```", ""]


def _render_mode(util: str, mode: str, desc: str, text: str) -> str:
    out = [f"# {util} — режим `{mode}`", "", _nav(util), ""]
    if desc:
        out += [desc, ""]
    out += ["## Справка", ""] + _fence(text)
    return "\n".join(out)


def _render_util(h: UtilHelp) -> str:
    out = [f"# {h.title}", "", _nav(h.name), ""]
    if h.modes:
        out += [f"## Режимы ({len(h.modes)})", ""]
        for mode, desc in h.modes:
            out.append(f"- [`{mode}`]({PREFIX}{h.name}-{mode}) — {desc}")
        out += [""]
    out += ["## Общая справка", ""] + _fence(h.help_text)
    return "\n".join(out)


def _render_index(helps: list[UtilHelp], version: str = "") -> str:
    ver = f" (версия {version})" if version else ""
    out = [
        "# Утилиты командной строки 1С:Предприятия 8",
        "",
        f"Справка CLI-утилит платформы{ver}: `rac`, `ibcmd`, `ibsrv`, `webinst`.",
        "Источник — вывод самих утилит (`help` и `help <режим>`).",
        "",
        "## Утилиты",
        "",
    ]
    for h in helps:
        n = len(h.modes)
        tail = f" — режимов: {n}" if n else ""
        out.append(f"- [`{h.name}`]({PREFIX}{h.name}) — {h.title}{tail}")
    out += [
        "",
        "## Поисковые подсказки",
        "",
        "- `rac` — кластер, менеджер, рабочий сервер/процесс, информационные базы,",
        "  сеансы, соединения, блокировки, профили безопасности, счётчики,",
        "  ограничения, требования назначения.",
        "- `ibcmd` — автономный сервер, журнал регистрации, информационная база,",
        "  конфигурации и расширения, сеансы, блокировки, хранилища двоичных данных.",
        "- `ibsrv` — запуск автономного сервера (порты, каталоги данных, СУБД,",
        "  лицензии, отладка, монитор производительности, SSH/HTTP).",
        "- `webinst` — публикация и удаление публикации веб-клиента (IIS, Apache),",
        "  виртуальный каталог, строка соединения, vrd-файл.",
        "",
    ]
    return "\n".join(out)


def _write_docs(helps: list[UtilHelp], out: Path, version: str = "") -> list[Path]:
    docs: dict[str, str] = {INDEX_STEM: _render_index(helps, version)}
    for h in helps:
        docs[f"{PREFIX}{h.name}"] = _render_util(h)
        for mode, desc in h.modes:
            stem = f"{PREFIX}{h.name}-{mode}"
            docs[stem] = _render_mode(h.name, mode, desc, h.mode_help.get(mode, ""))
    files: list[Path] = []
    for stem, body in docs.items():
        path = out / f"{stem}.md"
        path.write_text(body, encoding="utf-8")
        files.append(path)
    return files


def _util_version(bin_dir: Path) -> str:
    from v8help.config import discover_platforms

    for p in discover_platforms():
        if Path(p["bin_dir"]) == bin_dir:
            return p["version"]
    return bin_dir.parent.name if bin_dir.name.lower() == "bin" else ""


def generate(config, out_dir=None, bin_dir: Path | None = None,
             emit: ProgressFn | None = None) -> list[Path]:
    """Сгенерировать статьи утилит в ``out_dir`` (по умолчанию corpus_dir)."""
    bd = find_bin(bin_dir)
    if bd is None:
        (emit or (lambda s, m: None))("v8cli", "утилиты (rac/ibcmd) не найдены")
        return []
    out = Path(out_dir or config.corpus_dir)
    out.mkdir(parents=True, exist_ok=True)
    helps = collect(bd, emit=emit)
    return _write_docs(helps, out, _util_version(bd))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="v8help.v8cli", description="Собрать справку CLI-утилит 1С в корпус"
    )
    ap.add_argument("--bin", help="каталог bin платформы с утилитами")
    ap.add_argument("--out", help="каталог вывода (по умолчанию corpus_dir)")
    ap.add_argument("--config", help="путь к v8help.toml")
    args = ap.parse_args(argv)

    from v8help.server import load_config

    config, config_path = load_config(args.config)
    if config_path:
        base = Path(config_path).resolve().parent
        if not config.corpus_dir.is_absolute():
            config.corpus_dir = base / config.corpus_dir
    files = generate(
        config, out_dir=args.out, bin_dir=Path(args.bin) if args.bin else None,
        emit=lambda s, m: print(f"[{s}] {m}", file=sys.stderr, flush=True),
    )
    print(f"Записано статей: {len(files)} -> {args.out or config.corpus_dir}")
    return 0 if files else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

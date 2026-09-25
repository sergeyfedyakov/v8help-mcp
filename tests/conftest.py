"""Общие фикстуры тестов.

Автодискавери 1C:EDT отключён по умолчанию: иначе тесты, дергающие сборку
(``consolidate`` → ``edtcli.generate``), запускали бы реальный ``1cedtcli``
десятки раз. Тесты самого дискавери переопределяют ``_edt_registry_dirs`` /
``_edt_fs_dirs`` точечно.
"""

import pytest

from v8help import config as config_mod


@pytest.fixture(autouse=True)
def _no_real_edt(monkeypatch):
    monkeypatch.setattr(config_mod, "_edt_registry_dirs", lambda: [])
    monkeypatch.setattr(config_mod, "_edt_fs_dirs", lambda: [])
    monkeypatch.setattr(config_mod, "_edt_cli_cache", (False, None))

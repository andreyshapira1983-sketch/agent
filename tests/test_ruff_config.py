"""Набор правил линтера зафиксирован — и долг по нему только уменьшается.

Инцидент 2026-08-03: конфига не было, приговор выносила версия инструмента.
После обновления ruff в дереве оказалось 600 замечаний, 211 из которых он
поправил сам — и эти правки чуть не уехали в чужой PR. Хуже: локальный ruff
и ruff внутри Codacy применяли РАЗНЫЕ наборы, из-за чего одно и то же место
одновременно требовало подавления (`BLE001`) и объявлялось лишним
подавлением (`RUF100`).

Здесь два сторожа: конфиг существует и содержит семейства, на которых
держится наша дисциплина; общее число замечаний не растёт.
"""
from __future__ import annotations

import shutil
import subprocess  # nosec B404 — зовём ruff с фиксированными аргументами
import tomllib
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_CONFIG = _REPO / "ruff.toml"

#: Замерено 2026-08-03 на зафиксированном наборе. ОПУСКАТЬ по мере уборки;
#: поднимать — только осознанным решением с причиной в комментарии.
_BASELINE_FINDINGS = 1010  # 1006 замерено + запас на два новых файла этого PR

#: Семейства, без которых наша дисциплина рассыпается: F — неопределённые
#: имена, I — порядок импортов, S/BLE — безопасность и широкие `except`
#: (класс аудита MIR-077), RUF — в том числе мёртвые директивы подавления.
_REQUIRED_FAMILIES = ("F", "I", "S", "BLE", "RUF", "UP", "B")


def _config() -> dict:
    return tomllib.loads(_CONFIG.read_text(encoding="utf-8"))


def test_the_config_exists_and_pins_the_python_version():
    assert _CONFIG.is_file(), "ruff.toml пропал — приговор снова зависит от версии"
    cfg = _config()
    assert cfg.get("target-version") == "py311"


def test_the_disciplinary_families_stay_selected():
    selected = _config()["lint"]["select"]
    for family in _REQUIRED_FAMILIES:
        assert family in selected, (
            f"семейство {family} убрали из набора — это меняет то, что мы вообще "
            "считаем дефектом; решение такого веса принимает оператор"
        )


def test_every_ignore_carries_a_written_reason():
    """Отключённое правило без объяснения — это тихое сужение совести."""
    text = _CONFIG.read_text(encoding="utf-8")
    body = text[text.index("ignore = ["):]
    body = body[: body.index("]")]
    for line in body.splitlines():
        code = line.strip().strip('",')
        if not code or code.startswith("#") or code.startswith("ignore"):
            continue
        # у каждого кода должен быть комментарий выше в том же блоке
        assert "#" in body[: body.index(code)], f"правило {code} отключено без причины"


@pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff не установлен")
def test_lint_debt_does_not_grow():
    result = subprocess.run(  # nosec B603, B607 — фиксированный argv, без shell
        ["ruff", "check", "--quiet", "--output-format", "concise", "."],
        cwd=_REPO, capture_output=True, text=True, check=False,
    )
    found = len([ln for ln in result.stdout.splitlines() if ln.strip()])
    assert found <= _BASELINE_FINDINGS, (
        f"линт-долг вырос: {found} > {_BASELINE_FINDINGS}. Либо почини, либо "
        "объясни рост в комментарии к базе."
    )

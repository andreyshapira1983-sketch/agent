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
import subprocess  # nosec B404
import tomllib
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_CONFIG = _REPO / "ruff.toml"

#: Замерено 2026-08-03 на зафиксированном наборе. ОПУСКАТЬ по мере уборки;
#: поднимать — только осознанным решением с причиной в комментарии.
_BASELINE_FINDINGS = 1004  # точное измерение 2026-08-03, без запаса

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
    """Отключённое правило без объяснения — это тихое сужение совести.

    Причина обязана стоять В НЕПОСРЕДСТВЕННО предшествующем блоке
    комментариев: иначе новое правило проезжает под чужим объяснением
    (замечание ревью #298).
    """
    text = _CONFIG.read_text(encoding="utf-8")
    body = text[text.index("ignore = [") : text.index("]", text.index("ignore = ["))]
    lines = body.splitlines()[1:]          # первая строка — сам `ignore = [`
    reason_above = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            reason_above = False           # пустая строка обрывает блок причин
            continue
        if stripped.startswith("#"):
            reason_above = True
            continue
        for code in (c.strip().strip('"') for c in stripped.split(",")):
            if code:
                assert reason_above, f"правило {code} отключено без причины рядом"


@pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff не установлен")
def test_lint_debt_does_not_grow():
    # argv — только литералы. Подстановка сюда пути из `shutil.which` роняет
    # гейт Codacy (semgrep: «subprocess без статической строки»), а его
    # директивой `nosec` не отключить. Имя в PATH здесь безопасно: строка
    # фиксирована, внешнего ввода нет, оболочка не участвует.
    result = subprocess.run(  # nosec B603 B607
        ["ruff", "check", "--quiet", "--output-format", "concise", "."],  # noqa: S607
        cwd=_REPO, capture_output=True, text=True, check=False,
    )
    # 0 — чисто, 1 — есть замечания; всё остальное значит, что ruff не
    # отработал (ошибка конфигурации или запуска), и считать нечего.
    assert result.returncode in (0, 1), (
        f"ruff не выполнил прогон (код {result.returncode}): {result.stderr[:400]}"
    )
    found = len([ln for ln in result.stdout.splitlines() if ln.strip()])
    assert found <= _BASELINE_FINDINGS, (
        f"линт-долг вырос: {found} > {_BASELINE_FINDINGS}. Либо почини, либо "
        "объясни рост в комментарии к базе."
    )

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
#: 761 → 781 — осознанно (#300): путь через per-file-ignores откатан, потому
#: что remote-режим Codacy его не читает; S603/S607 в tests/ подавляются по
#: месту, оставшиеся места класса вернулись в счёт до своих срезов.
_BASELINE_FINDINGS = 779  # 781 → 779: раскол `core/loop.py` убрал два замечания попутно

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


def _rules_missing_local_reason(text: str) -> list[str]:
    """Коды из блока `ignore`, у которых нет причины прямо над ними.

    Комментарий действует ровно на ОДНУ следующую строку правил. Иначе
    дописанное снизу правило проезжает под чужим объяснением (замечания
    ревью #298). Группу правил с общей причиной пишем в одну строку.
    """
    body = text[text.index("ignore = [") : text.index("]", text.index("ignore = ["))]
    missing: list[str] = []
    reason_above = False
    for line in body.splitlines()[1:]:     # первая строка — сам `ignore = [`
        stripped = line.strip()
        if not stripped:
            reason_above = False           # пустая строка обрывает блок причин
            continue
        if stripped.startswith("#"):
            reason_above = True
            continue
        if not reason_above:
            missing.extend(
                code for code in (c.strip().strip('"') for c in stripped.split(","))
                if code
            )
        reason_above = False               # причина израсходована этой строкой
    return missing


def test_every_ignore_carries_a_written_reason():
    """Отключённое правило без объяснения — это тихое сужение совести."""
    missing = _rules_missing_local_reason(_CONFIG.read_text(encoding="utf-8"))
    assert not missing, f"правила отключены без причины рядом: {missing}"


def test_the_reason_check_catches_a_rule_hiding_behind_a_neighbour():
    """Доказательство самой проверки: чужая причина не покрывает соседа."""
    sample = (
        'ignore = [\n'
        '    # причина ровно для одного правила\n'
        '    "AAA001",\n'
        '    "BBB002",\n'
        ']'
    )
    assert _rules_missing_local_reason(sample) == ["BBB002"]


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

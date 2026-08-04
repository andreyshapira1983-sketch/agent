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
_BASELINE_FINDINGS = 405  # 779 → … → 419 → 405. Партия 1: 174 находки были не
#: долгом, а шумом. 96 × S105 — слово `token` из лексера командной строки
#: против эвристики про пароли (точность правила 0 из 96; настоящие секреты
#: ловит gitleaks по всей истории), и 78 × S101 — обычные assert'ы во втором
#: дереве тестов, которое шаблон `tests/**` не покрывал.
#: Партия 3а: 28 × RUF059 (неиспользованная распаковка помечена `_имя`)
#: и 35 × C408 (`dict(...)` -> литерал). Обе — автоправка ruff под
#: `--unsafe-fixes`, поведение не менялось: 6873 passed до и после.
#: Партия 3б: 37 × ISC004 — склейка строк внутри коллекции обёрнута
#: явными скобками. Все 37 просмотрены поимённо: забытых запятых нет
#: ни одной, но правило теперь чистое и поймает первую же настоящую.
#: Партия 4: 85 механических правок (RET504, PIE810, SIM103, FURB171,
#: PLW0108, RUF015, C416, PLC0414, SIM110, B905, FURB162 и часть
#: SIM102). Отклонены с разбором RUF005, SIM108, FLY002, E702 и
#: остаток SIM102 — там правка ухудшает читаемость, а не улучшает.
#: Партия 5: хвост одиночек. Исправлены четыре настоящих класса
#: (B904 — потеря цепочки причин, B011 — `assert False` под -O,
#: RUF006 — задача без ссылки, RUF012). Отклонены как ложные по
#: устройству: B008 (идиома FastAPI), PLW0603 (обработчик сигнала
#: и синглтон), DTZ001 (наивная дата — предмет теста), S112 (обе
#: с записанной причиной, учтены в MIR-077).
#: Партия 5-хвост: B017 (5) — `pytest.raises(Exception)` сужен до
#: настоящего исключения, измеренного на месте, а не угаданного;
#: RUF043 (4) — литеральная точка через `re.escape`, намеренная
#: альтернатива через сырую строку; SIM115 (3) — файл закрывается
#: сам, а не когда доберётся сборщик.

#: Семейства, без которых наша дисциплина рассыпается: F — неопределённые
#: имена, I — порядок импортов, S/BLE — безопасность и широкие `except`
#: (класс аудита MIR-077), RUF — в том числе мёртвые директивы подавления.
_REQUIRED_FAMILIES = ("F", "I", "S", "BLE", "RUF", "UP", "B")


def _config() -> dict:
    return tomllib.loads(_CONFIG.read_text(encoding="utf-8"))


def test_no_comment_is_mistaken_for_a_suppression_directive():
    """Пояснение, в котором написан код подавления, ruff читает как директиву.

    Дважды за одну сессию: сначала в `core/data_classifier.py`, где комментарий
    начинался со слова-директивы и стал сплошным подавлением всей строки, потом
    в `core/loop.py`, где директива была УПОМЯНУТА внутри объяснения — и ruff
    споткнулся о русский текст следом, а сам импорт остался незащищённым.

    Запись в блокноте ошибок второго раза не предотвратила, поэтому здесь
    сторож: ruff сам сообщает о таких директивах предупреждением, и это
    предупреждение не должно появляться ни разу.
    """
    # БЕЗ `--quiet`: именно этот флаг гасит предупреждение о негодной
    # директиве, и первая версия сторожа из-за него не срабатывала никогда
    # (проверено подсадкой настоящей строки — сторож молчал).
    proc = subprocess.run(  # noqa: S603  # nosec B603 B607
        ["ruff", "check", "."],  # noqa: S607
        cwd=_REPO, capture_output=True, check=False,
        # Кодировку задаём явно: на Windows `text=True` берёт локаль
        # (cp1251), а ruff цитирует строки исходника — в этом репозитории
        # с русскими комментариями. Без этого тест падал бы
        # UnicodeDecodeError, и падал бы только у части разработчиков.
        text=True, encoding="utf-8", errors="replace",
    )
    bad = [
        line for line in (proc.stdout + proc.stderr).splitlines()
        if "Invalid `# noqa` directive" in line
    ]
    assert not bad, (
        "комментарий прочитан как директива подавления:\n  "
        + "\n  ".join(bad)
        + "\nНе пишите код подавления словами в поясняющем тексте."
    )


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
        cwd=_REPO, capture_output=True, check=False,
        # Кодировку задаём явно: на Windows `text=True` берёт локаль
        # (cp1251), а ruff цитирует строки исходника — в этом репозитории
        # с русскими комментариями. Без этого тест падал бы
        # UnicodeDecodeError, и падал бы только у части разработчиков.
        text=True, encoding="utf-8", errors="replace",
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

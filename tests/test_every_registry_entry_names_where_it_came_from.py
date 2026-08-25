"""Каждая запись реестра называет, откуда взялось решение её завести.

Замер, отвергнутые варианты и границы: MIR-155 в docs/audit/MASTER_ISSUE_REGISTRY.md.
"""
from __future__ import annotations

import pathlib
import re

_REGISTRY = pathlib.Path(__file__).resolve().parents[1] / "docs/audit/MASTER_ISSUE_REGISTRY.md"

#: Метка следующего поля: значение Provenance кончается там, где начинается
#: следующая пара «**Имя:**». Обрывать по любому жирному тексту нельзя —
#: значение само бывает жирным (`**found by the live agent**`), и первый
#: прогон 2026-08-25 на этом дал два ложных пропуска из четырёх.
_FIELD = re.compile(r"\*\*[A-Z][A-Za-z ]{2,20}:\*\*")


def _entries(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for part in re.split(r"\n(?=### MIR-)", text):
        head = re.match(r"### (MIR-[0-9]+)", part)
        if head:
            out.append((head.group(1), part))
    return out


def _entries_without_provenance(text: str) -> list[str]:
    missing: list[str] = []
    for number, body in _entries(text):
        at = re.search(r"\*\*Provenance:\*\*", body)
        if at is None:
            missing.append(number)
            continue
        rest = body[at.end():]
        nxt = _FIELD.search(rest)
        value = (rest[:nxt.start()] if nxt else rest).strip(" .\n")
        if not value or value.lower() in {"none", "n/a"}:
            missing.append(number)
    return missing


def test_the_check_can_see_a_missing_field() -> None:
    """Контроль: без него разбор, не находящий ничего, проходил бы всегда.

    Проверка, у которой нет положительного случая, печатает ноль и о состоянии
    записи не говорит ничего — на этом уроке стоит вся сегодняшняя перепись.
    """
    synthetic = (
        "### MIR-901 — an entry that never said where it came from\n"
        "- **Aliases:** none. **Related:** nothing.\n\n"
        "### MIR-902 — an entry that did\n"
        "- **Aliases:** none. **Provenance:** operator asked for it 2026-08-25.\n"
    )

    assert _entries_without_provenance(synthetic) == ["MIR-901"]


def test_a_bold_value_is_not_mistaken_for_the_next_field() -> None:
    """Граница: значение само бывает жирным, и обрывать по нему нельзя."""
    synthetic = (
        "### MIR-903 — bold provenance\n"
        "- **Aliases:** none. **Provenance:** **found by the live agent**, 2026-08-04.\n"
    )

    assert _entries_without_provenance(synthetic) == []


def test_every_entry_names_its_provenance() -> None:
    """Решение оператора 2026-08-25: новая запись обязана называть исток.

    Прошлое задним числом не восстанавливается — но с этого дня вопрос «кто
    решил завести это» отвечается по документу, а не по памяти.
    """
    missing = _entries_without_provenance(_REGISTRY.read_text(encoding="utf-8"))

    assert not missing, (
        "записи реестра не называют, откуда взялось решение их завести: "
        + ", ".join(missing)
    )

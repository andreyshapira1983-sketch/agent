"""Событие опровержения не имеет права называть чужой гейт.

ЗАМЕР 2026-08-10, дважды на живых прогонах агента. В журнале:

    claims_refuted_by_arithmetic  count=1,
        reasons=[{'code': 'cited_literal_absent', 'expected': 'verify_claim'}]
    claims_refuted_by_arithmetic  count=12,
        reasons=[{'code': 'cited_literal_absent',
                  'expected': 'extract_lesson_from_failure, usage…'}]

Опровержение шло от гейта ЛИТЕРАЛОВ, а вывеска называла арифметику. Будущий
аудитор атрибутировал бы находку не тому механизму — ровно тот класс, который
эта система чинит весь день: одно имя над разными предметами.

Гейтов, дающих `ClaimReason`, теперь четыре: независимость памяти, арифметика,
статистические цифры и отсутствующий литерал. Общее у них — предмет («это
утверждение не следует из процитированной улики»), а не способ. Имя события
обязано называть предмет.

Дефект мой: четвёртый гейт добавил я и оставил соседнюю вывеску нетронутой.
"""
from __future__ import annotations

import ast
from pathlib import Path

import core.loop_verify_replan as vr

_SOURCE = Path(vr.__file__).read_text(encoding="utf-8")


def _logged_event_names() -> set[str]:
    """Имена событий, которые модуль пишет в журнал — из AST, не из греп."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(_SOURCE)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "log"):
            continue
        if node.args and isinstance(node.args[0], ast.Constant):
            value = node.args[0].value
            if isinstance(value, str):
                names.add(value)
    return names


def test_the_refutation_event_does_not_name_arithmetic() -> None:
    """ГЛАВНОЕ: вывеска перестаёт приписывать находку одному из четырёх."""
    assert "claims_refuted_by_arithmetic" not in _logged_event_names(), (
        "событие всё ещё называет арифметику, хотя несёт причины четырёх гейтов"
    )


def test_the_event_still_exists_under_a_truthful_name() -> None:
    """Ломка наоборот: переименование не должно стать удалением.

    Событие — единственное место, где причины опровержения доходят до журнала.
    Молча его потерять было бы хуже неверного имени.
    """
    assert "claims_refuted_by_content" in _logged_event_names()


def test_the_payload_still_carries_the_per_gate_code() -> None:
    """Имя стало общим — различение осталось внутри, в `code`.

    Иначе починка обменяла бы неверную атрибуцию на её отсутствие.
    """
    assert '"reasons": [c.reason.to_log_payload() for c in refuted[:5]]' in _SOURCE
    assert '"count": len(refuted)' in _SOURCE

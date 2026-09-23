"""Номера строк не превращают программу в прозу.

Замер 2026-09-23 на живой памяти агента (217 записей). Правило «программы не
утверждают фактов» (MIR-054) обходилось украшением адреса: `file_read` с
окном отдаёт адрес вида `core/x.py:284-292`, и проверка по ОКОНЧАНИЮ на нём
не срабатывала — `core/loop_response_deciders.py` узнавался как код, а
`core/loop_response_deciders.py:265-285` нет.

Цена: **18 записей памяти** собраны из его же кода и комментариев и помечены
как факты о мире с уверенностью 0.85. Среди них обрывок «SEARCH/REPLACE в
Aider).» из шапки `tools/patch_check.py`, записанный ДВАЖДЫ, и куски,
оборванные на середине фразы: «a persistent record (obtained_via="memory") is
judged on its».

Замер шире: из 217 записей 51 (24%) — обрывки с «Source: file:… Confidence:»,
102 (47%) — стенограммы «Вопрос: … Вывод: …». То есть 71% памяти не является
переносимым знанием. Часть этого — прямое следствие обойдённого правила.

Класс тот же, что у денежного числа со знаком валюты (7c4c957) и у урезанного
имени следа (df8c2cb): УКРАШЕННАЯ строка ломает сверку по окончанию. Лечится
нормализацией адреса ДО проверки, а не новым списком исключений — список
заплат закрывает по одной лазейке, и находится следующая.
"""
from __future__ import annotations

import pytest

from core.knowledge_pipeline import _bare_locator, _is_code_locator


@pytest.mark.parametrize(
    ("locator", "is_code"),
    [
        ("core/loop_response_deciders.py", True),
        # Живой случай: номера строк обходили правило.
        ("core/loop_response_deciders.py:265-285", True),
        ("tools/patch_check.py:1-60", True),
        ("scripts/deploy.sh:10", True),
        ("config/app.yaml:3-9", True),
        ("core/models.py:1", True),
        # Проза остаётся прозой, с номерами и без.
        ("knowledge_library/cs/book.txt", False),
        ("knowledge_library/cs/book.txt:100-120", False),
        ("docs/plan.md:5-9", False),
        ("proposals/selffix/sii_x/edits.txt", False),
        # Ссылка на строку в сети — не файл рабочей папки.
        ("https://example.invalid/page#L10-L20", False),
    ],
)
def test_a_line_range_does_not_turn_code_into_prose(locator: str, is_code: bool) -> None:
    assert _is_code_locator(locator) is is_code


def test_the_bare_locator_strips_every_decoration() -> None:
    assert _bare_locator("core/x.py:284-292") == "core/x.py"
    assert _bare_locator("core/x.py:284") == "core/x.py"
    assert _bare_locator("core/x.py") == "core/x.py"
    # Несколько украшений подряд снимаются все.
    assert _bare_locator("core/x.py:10:20") == "core/x.py"


def test_a_code_claim_is_refused_by_the_write_policy() -> None:
    """Сквозная проверка: правило отказа действительно срабатывает.

    Проверять только `_is_code_locator` мало — он мог бы отвечать верно, а
    политика записи его не спрашивать. Замер 2026-09-23 показал ровно
    обратное: спрашивала, но получала неверный ответ.
    """
    from core.knowledge_pipeline import KnowledgeWritePolicy
    from core.source_registry import ClaimRecord, SourceRecord

    source = SourceRecord(
        id="s1", type="file", title="x",
        locator="core/loop_response_deciders.py:265-285", trust_level=0.9,
    )
    claim = ClaimRecord(id="c1", source_id="s1", text="Нечто из кода", confidence=0.85)
    decision = KnowledgeWritePolicy().decide(claim, source=source)
    assert decision.decision == "reject"
    assert any("programs do not" in r or "code file" in r for r in decision.reasons)

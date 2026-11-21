"""R8 и R9 (2026-08-13, живые трейсы bd02fff1 и ac75fc92): честность — не ложь.

R8: гейт литералов опровергал истинные утверждения против УСЕЧЁННОЙ улики —
`evidence_budget` вырезал часть с литералом (`kept 9617 of 12191`), и «нет в
вырезке» читалось как «нет в источнике». Семь ложных REFUTED за один ход.
Инвариант — пятый гейт, дословно: неполный поиск не доказывает отсутствия;
усечённая улика (маркер `[INTENT-BUDGET:`/`[TOTAL-BUDGET:`) не имеет права
опровергать по отсутствию. Доказывать ПРИСУТСТВИЕ она может по-прежнему.

R9: детектор противоречий матчил ПРЕДМЕТЫ, а не суждения: «файл есть в
листинге» (Facts) + «содержимое файла не читал» (Unverified) давало
self_contradicted×4, перепись тела и карантин. Лучший эпистемический ответ дня
получил худший балл — чем честнее граница знания, тем сильнее наказание.
Инвариант: заявление о границе знания («не читал», «нет данных», «не
передавали») не оспаривает утверждение о существовании; противоречие — это
снятие ТОГО ЖЕ суждения.
"""
from __future__ import annotations

from core.answer_contradiction import contradicted_claims


def test_a_real_self_contradiction_is_still_caught() -> None:
    """ПРЕДОХРАНИТЕЛЬ R9: живой класс 2026-08-10 не открывается обратно."""
    answer = (
        "Conclusion: планировщик использует core/loop_attempt.py. [file:core/]\n"
        "Facts:\n- core/loop_attempt.py обрабатывает попытки [file:core/]\n"
        "Sources:\n1. file:core/ - listing\n"
        "Confidence: high\n"
        "Unverified:\n- Утверждение про core/loop_attempt.py не подтвердилось "
        "и может быть неверным.\n"
    )
    subjects = [c.subject for c in contradicted_claims(answer)]
    assert "core/loop_attempt.py" in subjects

"""Два корня probe_r1 в верификаторе: R3 и R2.

R3, ЖИВОЙ СЛУЧАЙ B3 (`trace_9cd331c`): истинное перекрёстное утверждение
(«notes_a от 01.08, notes_b от 09.08»), цитирующее ОБЕ улики, получило два
`cited_literal_absent`: гейт проверял каждую цитату против всего куска — в
notes_b нет литерала «notes_a», в notes_a нет «notes_b». Инвариант: салиентные
литералы куска накрываются ОБЪЕДИНЕНИЕМ процитированных улик (текст + адрес
источника), а не каждой уликой порознь.

R2, ЖИВОЙ СЛУЧАЙ B1 (`trace_4f295f8f`): заключение заявило «пять полок» и
перечислило четыре; «три позиции с qty<6» — и перечислило две, третью само же
отвергло («не подходит»). Оба куска ушли `unverified`, ответ — success с
допуском в обучение. Инвариант: заявленный счёт, противоречащий СОБСТВЕННОМУ
перечислению в том же предложении, — доказанная ложь, и подтверждённая цитата
её не отмывает: противоречие внутреннее, улика о нём ничего не знает.
"""
from __future__ import annotations

from core.evidence import ProvenanceChain, make_evidence
from core.verifier import verify


def _chain(*evs) -> ProvenanceChain:
    chain = ProvenanceChain()
    for ev in evs:
        chain.add(ev)
    return chain


def _file(source_id: str, excerpt: str):
    return make_evidence(kind="file", source_id=source_id, obtained_via="test",
                         claim="", excerpt=excerpt, confidence=0.9)


_NOTES_A = "# Заметки по стенду, версия от 2026-08-01\n- TODO: калибровка\n"
_NOTES_B = "# Заметки по стенду, версия от 2026-08-09\n- DONE: калибровка\n"


def test_a_cross_source_claim_is_covered_by_the_union() -> None:
    """ГЛАВНОЕ R3: перекрёстное утверждение — не ложь, если объединение улик
    накрывает его литералы. Форма B3 дословно."""
    answer = (
        "Conclusion: notes_b is newer. [file:probe_r1/notes_b.md]\n"
        "Facts:\n"
        "- Файл notes_a.md датирован 2026-08-01, notes_b.md — 2026-08-09 "
        "[file:probe_r1/notes_a.md] [file:probe_r1/notes_b.md]\n"
        "Sources:\n1. file:probe_r1/notes_a.md - a\n2. file:probe_r1/notes_b.md - b\n"
        "Confidence: high\nUnverified: nothing\n"
    )
    report = verify(
        answer=answer,
        chain=_chain(_file("probe_r1/notes_a.md", _NOTES_A),
                     _file("probe_r1/notes_b.md", _NOTES_B)),
        user_question="какая версия новее",
    )
    assert report.refuted_chunks == 0, [
        (c.verdict, getattr(c.reason, "expected", None)) for c in report.chunks
    ]


def test_a_multi_literal_claim_is_covered_part_by_part() -> None:
    """Живой a5813910: reason склеивает до трёх литералов через запятую, и
    поиск склейки целиком не находил ничего — объединение не снимало исков.
    Каждый литерал ищется отдельно; адрес источника — тоже улика."""
    answer = (
        "Conclusion: сравнение notes_a.md и notes_b.md сделано. "
        "[file:probe_r1/notes_a.md] [file:probe_r1/notes_b.md]\n"
        "Facts:\n"
        "- Файлы notes_a.md и notes_b.md различаются датой "
        "[file:probe_r1/notes_a.md] [file:probe_r1/notes_b.md]\n"
        "Sources:\n1. file:probe_r1/notes_a.md - a\n2. file:probe_r1/notes_b.md - b\n"
        "Confidence: high\nUnverified: nothing\n"
    )
    report = verify(
        answer=answer,
        chain=_chain(_file("probe_r1/notes_a.md", "версия 2026-08-01\n"),
                     _file("probe_r1/notes_b.md", "версия 2026-08-09\n")),
        user_question="что различается",
    )
    assert report.refuted_chunks == 0, [
        (c.verdict, getattr(c.reason, "expected", None)) for c in report.chunks
    ]


def test_a_single_source_lie_is_still_refuted() -> None:
    """ПРЕДОХРАНИТЕЛЬ R3: класс MIR-060 №2 не открывается обратно."""
    answer = (
        "Conclusion: The producer is core/self_build_memory.py. [file:core/loop.py]\n"
        "Facts:\n- The producer is core/self_build_memory.py [file:core/loop.py]\n"
        "Sources:\n1. file:core/loop.py - loop\n"
        "Confidence: high\nUnverified: nothing\n"
    )
    report = verify(
        answer=answer,
        chain=_chain(_file("core/loop.py", "from core.loop_memory_write import X\n")),
        user_question="who produces",
    )
    assert report.refuted_chunks >= 1


def test_a_claimed_count_contradicting_its_own_enumeration_is_refuted() -> None:
    """ГЛАВНОЕ R2: «пять полок (A1, B2, C4, D0)» — форма B1 дословно."""
    answer = (
        "Conclusion: Файл содержит пять полок (A1, B2, C4, D0). "
        "[file:probe_r1/inventory.txt]\n"
        "Facts:\n- В файле присутствуют полки A1, B2, C4 и D0 "
        "[file:probe_r1/inventory.txt]\n"
        "Sources:\n1. file:probe_r1/inventory.txt - inv\n"
        "Confidence: high\nUnverified: nothing\n"
    )
    report = verify(
        answer=answer,
        chain=_chain(_file("probe_r1/inventory.txt",
                           "item: axle-207, shelf: B2\nitem: rotor-33, shelf: A1\n"
                           "item: gasket-9, shelf: C4\nitem: stator-6, shelf: D0\n")),
        user_question="какие полки",
    )
    refuted = [c for c in report.chunks if c.verdict == "refuted"]
    assert refuted, [c.verdict for c in report.chunks]
    assert refuted[0].reason is not None
    assert refuted[0].reason.code == "count_mismatch"


def test_an_item_the_sentence_itself_rejects_is_not_counted() -> None:
    """Вторая ложь B1: «три позиции (…, stator-6: 7 шт. не подходит)» — само
    предложение исключило третий пункт, значит перечислено две."""
    answer = (
        "Conclusion: Есть три позиции с qty меньше 6 (rotor-33: 5 шт., "
        "gasket-9: 0 шт., stator-6: 7 шт. не подходит). "
        "[file:probe_r1/inventory.txt]\n"
        "Facts:\n- rotor-33 имеет qty 5 [file:probe_r1/inventory.txt]\n"
        "Sources:\n1. file:probe_r1/inventory.txt - inv\n"
        "Confidence: high\nUnverified: nothing\n"
    )
    report = verify(
        answer=answer,
        chain=_chain(_file("probe_r1/inventory.txt",
                           "item: rotor-33, qty: 5\nitem: gasket-9, qty: 0\n"
                           "item: stator-6, qty: 7\n")),
        user_question="какие позиции в дефиците",
    )
    assert any(
        c.verdict == "refuted" and c.reason and c.reason.code == "count_mismatch"
        for c in report.chunks
    ), [(c.verdict, getattr(c.reason, "code", None)) for c in report.chunks]


def test_a_correct_count_is_left_alone() -> None:
    """ПРЕДОХРАНИТЕЛЬ R2: верный счёт — и цифрой, и словом — не трогается."""
    for lead in ("четыре полки", "4 полки"):
        answer = (
            f"Conclusion: Файл содержит {lead} (A1, B2, C4, D0). "
            "[file:probe_r1/inventory.txt]\n"
            "Facts:\n- полки A1, B2, C4, D0 [file:probe_r1/inventory.txt]\n"
            "Sources:\n1. file:probe_r1/inventory.txt - inv\n"
            "Confidence: high\nUnverified: nothing\n"
        )
        report = verify(
            answer=answer,
            chain=_chain(_file("probe_r1/inventory.txt",
                               "shelf: A1\nshelf: B2\nshelf: C4\nshelf: D0\n")),
            user_question="какие полки",
        )
        assert report.refuted_chunks == 0, lead


def test_the_internal_contradiction_survives_a_resolved_citation() -> None:
    """Разрешившаяся цитата не отмывает внутреннее противоречие: улика ничего
    не знает о споре предложения с самим собой."""
    answer = (
        "Conclusion: Есть пять полок (A1, B2, C4, D0). [file:inv.txt]\n"
        "Facts:\n- полки A1, B2, C4, D0 [file:inv.txt]\n"
        "Sources:\n1. file:inv.txt - inv\n"
        "Confidence: high\nUnverified: nothing\n"
    )
    report = verify(
        answer=answer,
        chain=_chain(_file("inv.txt", "полки A1, B2, C4, D0 перечислены\n")),
        user_question="какие полки",
    )
    assert any(
        c.verdict == "refuted" and c.reason and c.reason.code == "count_mismatch"
        for c in report.chunks
    )

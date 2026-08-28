"""Подсудимый не правит улики прошлого, а правка судьи кричит с заявки.

Замер и решение: MIR-139 (слово оператора 2026-08-28: «флаг + denylist на
audit/ledger сейчас»).

Две оси одной двери, лечение разное:
- РЕЕСТР и ИСТОРИЧЕСКИЙ ЛЕДЖЕР — улики прошлого; правка меняет прошлые
  вердикты задним числом → полосе ЗАПРЕЩЕНО (денилист, раньше allowlist их
  пропускал — красный свидетель этой строки);
- tests/ — будущие судьи, но новые тесты и есть главный продукт агента →
  запрещать нельзя; вместо этого заявка обязана НЕСТИ ФЛАГ «правит судью»,
  чтобы глаз оператора не проскользнул (поле: self-authored verification
  unreliable — самооценка идеальна, пока реальность падает).
"""
from __future__ import annotations

from core.self_apply_lane import FileChange, classify_patch_risk, judge_touching_note


def test_the_case_files_are_denied_to_the_lane() -> None:
    """Красный свидетель: реестр и леджер полоса больше не принимает."""
    for path in ("docs/audit/MASTER_ISSUE_REGISTRY.md",
                 "docs/audit/HISTORICAL_FAILURE_LEDGER.md"):
        ok, _reason, _ = classify_patch_risk((FileChange(path=path, content="x"),))
        assert ok is False, f"{path}: улики прошлого не предмет самоприменения"


def test_other_audit_documents_are_still_proposable() -> None:
    """Граница узкая: денилист бьёт по уликам, не по всему docs/audit/."""
    ok, _reason, _ = classify_patch_risk((
        FileChange(path="docs/audit/BRANCH_SUMMARY.md", content="x"),))

    assert ok is True


def test_a_judge_touching_patch_carries_the_loud_flag() -> None:
    note = judge_touching_note(["tests/test_something.py", "core/loop.py"])
    silent = judge_touching_note(["core/loop.py", "docs/notes.md"])

    assert "СУДЬЮ" in note and "tests/" in note
    assert silent == ""


def test_both_proposal_builders_wear_the_flag_and_elevate_risk() -> None:
    """Обе точки рождения заявки: флаг в summary И повышенный класс риска.

    OWASP-уточнение оператора: правка судей — явно повышенный риск, чтобы её
    видели машинные ворота, а не только глаз.
    """
    import inspect

    from core import self_build_producer as mod

    src = inspect.getsource(mod)
    assert src.count("judge_touching_note") >= 2, (
        "обе inbox.add-точки полосы обязаны нести флаг «правит судью»")
    assert src.count('"irreversible" if _') >= 2, (
        "правка судей обязана поднимать класс риска в ОБЕИХ точках")

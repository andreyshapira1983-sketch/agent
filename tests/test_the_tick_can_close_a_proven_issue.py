"""Закрыть проблему можно только по доказательству, и доказывает свидетель.

Реестр проблем самоулучшения умел заводить записи и не умел их закрывать:
переход в resolved жил единственным местом — за операторской командой
`:self-issue-verify`. Из автономного тика входа туда не было. Замер
2026-08-29: 29 записей, все `open`, файл не менялся девять дней; кандидат
«почини провал» с весом 55 присутствовал всегда и все 282 цикла подряд
обходил причинную лестницу с её весом 45 — 409 наблюдений так и остались
непереваренными (docs/CODE_NOTES.md, «Орган обучения голодает, а не сломан»).

Охрана — авторства агента: право отказать берётся не из воли тика, а из
внешнего к нему прогона. Свидетель обязан покраснеть на объекте, сломанном
ИМЕННО этой проблемой, и позеленеть после починки; отсюда две метки в улике.
Отсутствие свидетеля — основание продолжать работу, а не закрывать запись.

Две дыры пришпилены отдельно, обе названы самим агентом: пустой свидетель,
зелёный на сломанном объекте, и чужой уже зелёный тест, привязанный к
проблеме, — ни один из них покраснения не предъявляет и потому не проходит.
"""
from __future__ import annotations

from pathlib import Path

from core.self_improvement_issues import (
    SelfImprovementIssueRegistry,
    close_proven_issue,
)

_OBSERVED_AT = "2026-08-29T00:00:00"


def _issue_with_runs(tmp_path: Path, *labels: str):
    """Реестр с одной проблемой, в улике которой лежат названные прогоны."""
    registry = SelfImprovementIssueRegistry(tmp_path / "issues.jsonl")
    fingerprint = registry.upsert_failure("bug text", _OBSERVED_AT).fingerprint
    for index, label in enumerate(labels, start=1):
        # Формат записи авторства агента: статус|время|метка. Цвет прогона
        # живёт в метке — первое поле несёт статус проблемы, и цвета там нет.
        registry.transition(
            status="open",
            observed_at=f"2026-08-29T00:00:0{index}",
            fingerprint=fingerprint,
            evidence=f"open|2026-08-29T00:00:0{index}|{label}",
        )
    return registry, fingerprint


def test_a_proven_fix_closes_the_issue(tmp_path):
    registry, fingerprint = _issue_with_runs(
        tmp_path, "red-before-fix", "green-after-fix",
    )

    assert close_proven_issue(registry, fingerprint) is True
    assert registry.list()[0].status == "resolved"


def test_without_a_red_run_the_issue_stays_open(tmp_path):
    registry, fingerprint = _issue_with_runs(
        tmp_path, "green-before-fix", "green-after-fix",
    )

    assert close_proven_issue(registry, fingerprint) is False
    assert registry.list()[0].status == "open"


def test_a_witness_green_on_the_broken_object_proves_nothing(tmp_path):
    registry, fingerprint = _issue_with_runs(tmp_path, "green-before-fix")

    assert close_proven_issue(registry, fingerprint) is False
    assert registry.list()[0].status == "open"


def test_a_foreign_green_test_cannot_close_the_issue(tmp_path):
    """Чужой тест зелен и на сломанном объекте, поэтому покраснения не предъявит."""
    registry, fingerprint = _issue_with_runs(tmp_path, "green-after-fix")

    assert close_proven_issue(registry, fingerprint) is False
    assert registry.list()[0].status == "open"


def test_the_seeded_failure_text_is_not_read_as_a_run(tmp_path):
    """При заведении в улику кладётся сырой текст провала без разделителей.

    Разбор обязан его пропустить, а не падать на нём.
    """
    registry, fingerprint = _issue_with_runs(
        tmp_path, "red-before-fix", "green-after-fix",
    )
    seeded = [e for e in registry.list()[0].evidence if "|" not in e]
    assert seeded, "проблема заводится с сырым текстом провала в улике"

    assert close_proven_issue(registry, fingerprint) is True


def test_an_unknown_fingerprint_changes_nothing(tmp_path):
    registry, _ = _issue_with_runs(
        tmp_path, "red-before-fix", "green-after-fix",
    )

    assert close_proven_issue(registry, "no-such-fingerprint") is False
    assert registry.list()[0].status == "open"

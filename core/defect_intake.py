"""Свой сбой становится дефектом реестра, а не только наблюдением.

Замер 2026-09-22: за день в data/self_improvement_issues.jsonl не появилось
ни одной записи ОТ АГЕНТА — все четыре завёл Claude. При этом собственные
детекторы сработали десятки раз и легли в data/causal_observations.jsonl:
агент их разбирал, объяснял, но работой они не становились. Сборщик дефектов
(`core/self_build_memory._recent_self_improvement_events`) смотрит только на
эпизоды про самоприменение — откаты и расколы, — и настоящих сбоев не видит.

Здесь наблюдение, сработавшее не один раз, заводится в реестр обычным путём
(`upsert_failure`): дальше его берёт путь самопочинки (`core/patch_route.py`).
Одиночное наблюдение не заводится: один раз — случай, два — класс.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

MIN_OCCURRENCES = 2
MAX_PER_SWEEP = 5


def intake_observations(workspace: Path | str, registry: Any = None) -> list[str]:
    """Завести повторяющиеся наблюдения дефектами; вернуть их отпечатки."""
    from core.causal_store import CausalObservationStore
    from core.self_improvement_issues import DEFAULT_ISSUE_PATH, SelfImprovementIssueRegistry

    root = Path(workspace)
    registry = registry or SelfImprovementIssueRegistry(root / DEFAULT_ISSUE_PATH)
    # Уже принятое наблюдение узнаётся по СВОЕЙ улике (cobs_…), которую приём
    # кладёт в дефект. Сверять его отпечаток с `related_error_text` — сверять
    # разные вещи: та строка начинается со слова «detectors» и никогда не равна
    # отпечатку, поэтому за каждый проход заново переписывались одни и те же
    # пять классов и их «последний раз виден» обновлялся без причины
    # (замечено живой проверкой на сервере 2026-09-22, 39 наблюдений).
    known = {str(ref) for issue in registry.list() for ref in (issue.evidence or ())}
    filed: list[str] = []
    records = CausalObservationStore(root / "data" / "causal_observations.jsonl").load()
    for record in sorted(records, key=lambda r: (-r.occurrences, r.last_seen))[:40]:
        if record.occurrences < MIN_OCCURRENCES or record.fingerprint in known:
            continue
        # ОДИН дефект на КАЖДЫЙ сигнал, а не один на их сочетание. Реестр
        # считает класс по строке сигналов (`failure_fingerprint`), поэтому
        # сочетание заводило свой класс: замер 2026-09-23 — 15 открытых
        # записей при восьми различных сигналах, и `reasoning_action_mismatch`
        # жил сразу в восьми из них. Тот же промах, против которого механизм и
        # делался (MIR-035: 13 копий одного класса), только на шаг выше: класс
        # — это сигнал, а наблюдение с тремя сигналами есть улика для трёх.
        for signal in (record.defect_signals or ("detector",)):
            # Текст начинается со слова «detectors» — по нему реестр сам считает
            # отпечаток класса и заголовок (core/self_improvement_issues.py).
            text = (f"detectors {signal}: {record.observed_mismatch[:300]} "
                    f"(повторений: {record.occurrences}; улики: "
                    f"{', '.join(record.evidence_refs[:3])})")
            issue = registry.upsert_failure(text, record.last_seen)
            registry.transition(status=issue.status, observed_at=record.last_seen,
                                fingerprint=issue.fingerprint, evidence=record.fingerprint)
        filed.append(record.fingerprint)
        if len(filed) >= MAX_PER_SWEEP:
            break
    return filed

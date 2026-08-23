"""Дешёвый путь пропускает каталогизацию по ИСТИННОЙ причине.

ИСТОРИЧЕСКИЙ КЛАСС (H-05, docs/audit/HISTORICAL_FAILURE_LEDGER.md).
Therac-25, 1985–87, разбор Левесон и Тёрнера (1993): быстрый ввод оператора
проскакивал переход состояния, на который опиралась блокировка, и консоль
показывала состояние, в котором машина не находилась. Опасен был не быстрый
путь сам по себе, а РАСХОЖДЕНИЕ между тем, что система про себя утверждала, и
тем, чем она была.

ЛОКАЛЬНАЯ ФОРМА, ВОСПРОИЗВЕДЕНА 2026-08-23 сквозным прогоном. Ветка пропуска
конвейера знаний обосновывала себя словами «цепочка пуста, инструменты не
запускались». Замер на ходе «привет» с одной записью постоянной памяти:
дешёвый путь активен, а в цепочке ОДНА улика вида `memory` — премиса ложна.
Память складывается в цепочку РАНЬШЕ (`_fold_evidence_chain`), чем
принимается решение о каталогизации.

ПОЧЕМУ ПОВЕДЕНИЕ НЕ МЕНЯЕТСЯ. Прогонять конвейер знаний по цепочке из одной
лишь памяти было бы самоподтверждением: агент записал бы собственную запись
как новое знание (та же доктрина, что в MIR-046 — своя память не независимый
свидетель). Пропуск правилен; ложной была причина.

ЧТО ИСПРАВЛЕНО. Условие говорит теперь то, что и означало: пропускается ход,
в котором не появилось НИ ОДНОЙ улики от инструментов. На дешёвом пути их не
бывает по построению, поэтому поведение прежнее — но премиса стала истинной и
не может тихо разойтись с кодом.
"""
from __future__ import annotations

from core.evidence import ProvenanceChain, make_evidence


def _chain(*kinds: str) -> ProvenanceChain:
    chain = ProvenanceChain()
    for i, kind in enumerate(kinds):
        chain.add(make_evidence(
            kind=kind, source_id=f"{kind}:{i}", obtained_via="probe",
            claim="probe", excerpt="содержимое",
        ))
    return chain


def test_a_memory_only_chain_is_still_skipped() -> None:
    """Поведение сохранено: самоподтверждения не заводим."""
    from core.loop_evidence_chain import chain_has_tool_evidence

    assert chain_has_tool_evidence(_chain("memory")) is False
    assert chain_has_tool_evidence(_chain("memory", "user_explicit")) is False
    assert chain_has_tool_evidence(ProvenanceChain()) is False


def test_a_chain_with_tool_evidence_is_not_skippable() -> None:
    """Граница: улика от инструмента обязана быть каталогизирована."""
    from core.loop_evidence_chain import chain_has_tool_evidence

    assert chain_has_tool_evidence(_chain("web_page")) is True
    assert chain_has_tool_evidence(_chain("memory", "file")) is True
    assert chain_has_tool_evidence(_chain("shell_output")) is True


def test_the_premise_the_code_states_is_the_one_it_checks() -> None:
    """Сам исторический урок: утверждение системы о себе обязано совпадать с
    тем, что она проверяет. Ветка не имеет права ссылаться на пустоту
    цепочки, пока в цепочке лежит память."""
    import inspect

    from core.loop_evidence_chain import AgentLoopEvidenceChain

    src = inspect.getsource(AgentLoopEvidenceChain._rank_and_catalog_evidence)
    branch = src[src.index("if cheap_path_active"):]
    branch = branch[:branch.index("else:")]

    assert "chain_has_tool_evidence" in branch, (
        "ветка снова решает по одному лишь флагу дешёвого пути"
    )
    # Прежняя версия этой проверки запрещала саму ФРАЗУ «the chain is empty»
    # и падала на комментарии, который цитирует её как историю. Запрещать
    # цитирование прошлой ошибки — значит стирать её объяснение; проверять
    # надо решение, а не словарь. Поэтому здесь пинится журнальная причина:
    # она называет то же условие, что и ветка.
    assert "cheap_path_no_tool_evidence" in branch, (
        "журнальная причина разошлась с условием ветки"
    )

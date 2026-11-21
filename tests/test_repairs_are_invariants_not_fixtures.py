"""Попытка опровергнуть три вчерашние починки на ДРУГИХ предметах.

Регрессия 2026-08-10 показала починки на тех же случаях, ради которых они
делались. Это не отличает починенный инвариант от починенного примера. Здесь
каждый инвариант проверяется на предмете, которого в исходных фикстурах не
было, и каждая проверка написана как попытка сломать, а не подтвердить.

Три класса:
  обучение  — другой сигнал из того же запрещающего набора и другой предмет
              противоречия;
  секреты   — другая форма секрета и другой вид редактуры;
  запреты   — другой непредставимый запрет, другой язык.
"""
from __future__ import annotations

import pytest

from core.completion_contract import derive_completion_contract
from core.knowledge_pipeline import KnowledgeWritePolicy
from core.smart_memory import (
    DISQUALIFYING_DEFECT_SIGNALS,
    EpisodeRecord,
    decide_usage_eligibility,
    procedure_credit_allowed,
)
from core.unsupported_claims import apply_answer_enforcement


class _Src:
    def __init__(self, sid: str = "file:docs/CODE_NOTES.md") -> None:
        self.id = sid
        self.trust_level = 0.85
        self.trust = 0.85
        self.type = "file"
        self.title = sid
        self.locator = sid.split(":", 1)[-1]


class _Claim:
    def __init__(self, text: str, sid: str = "file:docs/CODE_NOTES.md") -> None:
        self.id = "claim_y"
        self.text = text
        self.source_id = sid
        self.status = "extracted"
        self.confidence = 0.85


class _Report:
    total_chunks = 6
    verified_chunks = 6
    unverified_chunks = 0
    cited_but_unmatched_chunks = 0
    self_declared_chunks = 0
    structural_chunks = 0
    topic_supported_but_claim_unverified_chunks = 0
    subagent_asserted_chunks = 0
    receipt_missing_chunks = 0
    dialogue_supported_chunks = 0
    user_asserted_chunks = 0
    chain_was_empty = False
    fully_unverified = False
    malformed_output = False
    chunks: tuple = ()


# ── КЛАСС 1: обучение ────────────────────────────────────────────────────────

#: Предмет другой: SHA коммита вместо идентификатора прогона, и утверждение
#: снято в Conclusion-паре, а не в той же паре Facts, что была в фикстуре.
_OTHER_CONTRADICTION = (
    "Conclusion: ветка собрана на 20698b1e4c2. [shell]\n"
    "Facts:\n- Дерево чисто на 20698b1e4c2, 7410 тестов. [shell]\n"
    "Sources:\n1. shell - git\n"
    "Confidence: high\n"
    "Unverified:\n- Не доказано, что 20698b1e4c2 — коммит этого прогона.\n"
)


def _episode(**kw) -> EpisodeRecord:
    base = {
        "goal": "g", "question": "q", "outcome": "success", "summary": "s",
        "full_answer": _OTHER_CONTRADICTION, "completion_state": "achieved",
        "verified_chunks": 6, "unverified_chunks": 0, "answer_quality_score": 1.0,
        "tools_used": ["shell_exec"], "source_labels": ["shell:git"],
        "used_procedure_ids": ["proc_other"],
    }
    base.update(kw)
    return EpisodeRecord(**base)


@pytest.mark.parametrize("signal", sorted(DISQUALIFYING_DEFECT_SIGNALS))
def test_every_named_signal_blocks_both_gates(signal: str) -> None:
    """Опровержение №2: инвариант объявлен НАБОРОМ, а не одним именем.

    Если завтра в набор добавят второй сигнал и забудут второй рубеж, этот
    тест покраснеет на нём, а не на том, ради которого набор заводили.
    """
    ep = _episode(defect_signals=[signal])
    assert not decide_usage_eligibility(ep)
    assert not procedure_credit_allowed(ep)


# ── КЛАСС 2: секреты ─────────────────────────────────────────────────────────


def test_an_unknown_redaction_kind_is_still_refused() -> None:
    """Опровержение №5: вид секрета, которого сканер ещё не знает."""
    decision = KnowledgeWritePolicy().decide(
        _Claim("SOME_FUTURE_TOKEN=[REDACTED:not-yet-invented-kind]"), source=_Src()
    )
    assert decision.decision == "reject"


def test_uppercase_marker_is_not_a_bypass() -> None:
    """Опровержение №6: регистр вида не должен открывать обход.

    ЧЕСТНО: образец требует нижний регистр, потому что `core/redaction.py`
    пишет вид только так. Если запись когда-нибудь сменит форму, этот тест
    покраснеет — и это именно то, чего от него хотят.
    """
    from core.redaction import _replacement  # форма токена берётся у автора

    produced = _replacement("openai-key")
    decision = KnowledgeWritePolicy().decide(
        _Claim(f"KEY={produced}"), source=_Src()
    )
    assert decision.decision == "reject", (
        f"политика не узнаёт токен, который производит сам модуль: {produced!r}"
    )


# ── КЛАСС 3: запреты ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("request_text", [
    "Разбери маршрутизацию. Не используй интернет и не открывай сеть.",
    "Analyse the router. Do not modify any configuration while doing it.",
    "Proanaliziruy marshrutizaciyu. Ne menyay konfiguraciyu.",
])
def test_other_prohibitions_reach_the_operator(request_text: str) -> None:
    """Опровержение №7: другой запрет, другой язык, та же честность."""
    contract = derive_completion_contract(request_text)
    kinds = {u.kind for u in contract.unsupported_deliverables}
    assert "prohibition" in kinds, f"запрет не распознан: {request_text!r}"

    answer = "Conclusion: разобрано. [file]\nFacts:\n- разобрано [file]\n"
    result = apply_answer_enforcement(
        answer=answer, report=_Report(), question=request_text, contract=contract
    )
    assert "не проверял" in result.answer or "not mechanically checked" in result.answer

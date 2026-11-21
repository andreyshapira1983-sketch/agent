"""Свои же файлы агента не становятся фактами о мире.

Замер 2026-09-23 на живой памяти: фактами с уверенностью 0.85 ложились его
же черновики правок (`proposals/selffix/…/edits.txt`, вместе с маркером
`<<<<<<< LINES`), его же ящик голоса (`data/chat_outbox.jsonl`, прямо со
скобками JSON) и строки таблицы сгенерированного из кода
`knowledge/generated/AGENT_ANATOMY.md`. При чистке памяти убрано 44 обрывка;
прогон нынешнего кода показал, что все три входа открыты.

Класс — OWASP ASI06 «отравление памяти» в тихой форме: своё, пересказанное
своими словами, выглядит как независимое знание, и по словам его не отличить.
Отличает только происхождение, поэтому правило стоит на адресе.
"""
from __future__ import annotations

from core.knowledge_pipeline import KnowledgeWritePolicy
from core.source_registry import ClaimRecord, SourceRecord


def _decide(locator: str) -> str:
    source = SourceRecord(id="s1", type="file", title="x", locator=locator, trust_level=0.9)
    claim = ClaimRecord(
        id="c1", source_id="s1",
        text="Строка принимается автоматом, если существует путь к принятию.",
        confidence=0.85,
    )
    return KnowledgeWritePolicy().decide(claim, source=source).decision


def test_an_own_draft_is_refused_as_a_fact() -> None:
    assert _decide("proposals/selffix/sii_eb1d0bc19f4a7b28/edits.txt") == "reject"
    assert _decide("data/chat_outbox.jsonl") == "reject"
    assert _decide("knowledge/generated/AGENT_ANATOMY.md") == "reject"

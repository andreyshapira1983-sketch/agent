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

import pytest

from core.knowledge_pipeline import KnowledgeWritePolicy, _is_own_artifact
from core.source_registry import ClaimRecord, SourceRecord


@pytest.mark.parametrize(
    ("locator", "own"),
    [
        # Живые случаи 2026-09-23.
        ("proposals/selffix/sii_eb1d0bc19f4a7b28/edits.txt", True),
        ("data/chat_outbox.jsonl", True),
        ("knowledge/generated/AGENT_ANATOMY.md", True),
        ("data/notes/20260923_rag_agent_proposal.md", True),
        ("data/self_improvement_issues.jsonl:1-40", True),
        ("logs/campaign_24h_stderr.log", True),
        ("/root/agent-main/data/chat_outbox.jsonl", True),
        ("./proposals/x/edits.txt", True),
        # Книги и документы, написанные людьми, остаются источниками.
        ("knowledge_library/cs/txt/Mogensen_BasicsOfCompilerDesign.txt", False),
        ("knowledge_library/cs/txt/Mogensen_BasicsOfCompilerDesign.txt:940-1075", False),
        ("math_study/library/txt/Judson_AbstractAlgebra.txt", False),
        ("knowledge/doctrine/CENTRAL_AGENT_GOVERNANCE.md", False),
        ("docs/plan.md:5-9", False),
        # Слово «data» внутри пути книги — не журнал агента.
        ("knowledge_library/cs/txt/data_structures.txt", False),
    ],
)
def test_the_agents_own_files_are_recognised(locator: str, own: bool) -> None:
    assert _is_own_artifact(locator) is own


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


def test_a_book_still_becomes_knowledge() -> None:
    """Сторож не шире основания: книга по-прежнему даёт факт."""
    assert _decide("knowledge_library/cs/txt/Mogensen_BasicsOfCompilerDesign.txt") == "save"
    assert _decide("knowledge/doctrine/CENTRAL_AGENT_GOVERNANCE.md") == "save"

"""Секрет со страницы не доходит ни до одной долговечной поверхности.

ИСТОРИЧЕСКИЙ КЛАСС (H-19, docs/audit/HISTORICAL_FAILURE_LEDGER.md).
Meltdown/Spectre (2018) и Rowhammer (Kim et al., 2014) — общая форма: сведения
пересекают границу по пути, который никто не задумывал как путь.

Здесь закрепляется НЕ существование сканера, а то, что он стоит на каждом
выходе. Улика с секретом создаётся законно — это текст страницы, и верификатор
обязан видеть, что там написано. Значение имеет, закрыт ли каждый путь ИЗ неё.
"""
from __future__ import annotations

import pytest

from core.memory_policy import MemoryWritePolicy
from core.redaction import redact_dlp_text

SECRETS = [
    ("openai-key", "sk-proj-abcdef0123456789abcdef0123456789abcdef0123456789"),
    ("url-credentials", "https://user:p4ssw0rd-secret@example.org/panel"),
    ("aws-secret-key", "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"),
]


@pytest.mark.parametrize(("kind", "secret"), SECRETS)
def test_the_memory_boundary_rejects_it(kind: str, secret: str) -> None:
    decision = MemoryWritePolicy().decide(f"Из отчёта: {secret}", tags=("fact",))

    assert decision.decision == "reject", decision
    assert any(kind in reason for reason in decision.reasons), decision.reasons


@pytest.mark.parametrize(("kind", "secret"), SECRETS)
def test_the_outbound_answer_is_redacted(kind: str, secret: str) -> None:
    """Последний рубеж: даже если модель процитировала ключ со страницы."""
    out, findings, _ = redact_dlp_text(f"Conclusion: {secret} [web:https://x/y]")

    assert findings, kind
    assert secret not in out
    assert "[REDACTED:" in out


def test_the_chain_log_payload_carries_no_excerpt() -> None:
    """Журнал трассы переживает прогон — выдержки в него не едут."""
    import json

    from core.evidence import ProvenanceChain, evidence_from_tool_result

    secret = SECRETS[0][1]
    chain = ProvenanceChain()
    chain.add(evidence_from_tool_result(
        tool_name="web_fetch", arguments={"url": "https://example.org/r"},
        output={"url": "https://example.org/r",
                "text": f"Отчёт. Ключ панели: {secret}. Выручка выросла.",
                "fetched_at": "2026-08-24T00:00:00Z"},
        status="success",
    ))

    assert secret not in json.dumps(chain.to_log_payload(), ensure_ascii=False)

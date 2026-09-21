"""На светскую реплику — ответ, а не отчёт о надёжности беседы.

2026-09-21, панель: «Привет, как дела?» → «⚠️ Не подтверждено: что ты хочешь
обсудить» и «Проверка: подтверждено 2 из 5 утверждений; уверенность: низкая».
"""
from __future__ import annotations

from types import SimpleNamespace

from core.loop_response_deciders import AgentLoopResponseDeciders
from core.response_draft import ResponseDraft


def _loop(logged):
    return SimpleNamespace(last_verification=object(), last_provenance=None,
                           last_confidence_vector=None, last_evidence_support=None,
                           log=SimpleNamespace(log=lambda e, p: logged.append(e)))


def test_a_greeting_gets_no_tail() -> None:
    logged: list = []
    draft = ResponseDraft(body="Привет! Всё хорошо.")
    AgentLoopResponseDeciders._add_verification_summary(_loop(logged), draft, "Привет, как дела?")
    assert "verification_tail_skipped" in logged
    assert "Проверка:" not in draft.render()

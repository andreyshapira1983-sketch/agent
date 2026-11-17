"""Полужирные заголовки разделов («**Conclusion:**») — тоже заголовки: человек получает ответ, а не кухню."""
from __future__ import annotations

import pytest

from core.answer_format import format_human_response

_BOLD = (
    "**Conclusion:**\n"
    "Последняя стабильная версия Python — 3.15.2, источник — https://www.python.org/downloads/ "
    "[verified:web:https://www.python.org/downloads/]\n"
    "**Facts:**\n"
    "- На странице стоит «Download Python 3.15.2» [verified:web:https://www.python.org/downloads/]\n"
    "**Sources:**\n"
    "1. web:https://www.python.org/downloads/\n"
    "**Confidence:** medium\n"
    "**Unverified:** nothing\n"
    "**Safety:** nothing\n"
)


@pytest.mark.parametrize("chat", ["1", None])
def test_bold_headers_are_sections(monkeypatch: pytest.MonkeyPatch, chat: str | None) -> None:
    if chat:
        monkeypatch.setenv("AGENT_HUMAN_CHAT", chat)
    else:
        monkeypatch.delenv("AGENT_HUMAN_CHAT", raising=False)
    shown = format_human_response(_BOLD)
    assert shown.startswith("Последняя стабильная версия Python — 3.15.2"), shown
    assert "Conclusion" not in shown and "Sources" not in shown and "Safety" not in shown, shown


def test_bold_and_plain_headers_read_the_same(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_HUMAN_CHAT", "1")
    assert format_human_response(_BOLD) == format_human_response(_BOLD.replace("**", ""))


def test_a_bold_line_inside_facts_is_not_a_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_HUMAN_CHAT", raising=False)
    answer = "Conclusion: готово [general-knowledge]\nFacts:\n**Сбор данных:**\n- шаг один [general-knowledge]\nUnverified: nothing\n"
    shown = format_human_response(answer)
    assert "Сбор данных:" in shown and "шаг один" in shown, shown

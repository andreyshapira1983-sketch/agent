"""Полужирные заголовки разделов («**Conclusion:**») — тоже заголовки: человек получает ответ, а не кухню."""
from __future__ import annotations

import pytest

from core.answer_format import format_human_response


def test_a_bold_line_inside_facts_is_not_a_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_HUMAN_CHAT", raising=False)
    answer = "Conclusion: готово [general-knowledge]\nFacts:\n**Сбор данных:**\n- шаг один [general-knowledge]\nUnverified: nothing\n"
    shown = format_human_response(answer)
    assert "Сбор данных:" in shown and "шаг один" in shown, shown

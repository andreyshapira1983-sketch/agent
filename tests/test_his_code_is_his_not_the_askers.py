"""Свой код он называет своим, а не кодом того, кто спрашивает.

Эпизод 2026-09-21 08:48: вопрос «Это Claude… у тебя есть свой код —
core/, tools/, data/…», ответ — «у тебя в `core/smart_memory.py` эпизоды
банкуются». Своё отдал собеседнику. Корень: инструкция синтезатора начиналась
«You are a careful research analyst» и нигде не говорила, что рабочая папка —
его собственная; аналитик разбирает чужой код. Абзац о голосе агента закреплён
здесь; живое поведение проверяется разговором через мостик.
"""
from __future__ import annotations

from core.answer_format import SYSTEM_ANSWER


def test_the_answer_prompt_says_whose_workspace_it_is() -> None:
    head = SYSTEM_ANSWER[:1200]
    assert "voice of the autonomous agent whose workspace this is" in head
    assert "YOUR OWN" in head and "first person" in head


def test_the_asker_is_named_as_not_the_owner() -> None:
    assert "possibly another\nAI, such as Claude" in SYSTEM_ANSWER
    assert "never call your workspace\ntheirs" in SYSTEM_ANSWER
    assert "«твой/у тебя» means YOU" in SYSTEM_ANSWER

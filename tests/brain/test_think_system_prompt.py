"""Tests for Brain.think system_prompt threading.

We verify the persona forwarded by ChatHandler actually lands in the
context dict the LLM adapter receives. No real OpenAI call — a fake
LLMInterface captures the context.
"""

from __future__ import annotations

from brain.core import Brain
from brain.interfaces.llm_interface import LLMInterface
from brain.memory.memory_manager import MemoryManager


class _CapturingLLM(LLMInterface):
    """LLM stub that just records the context dict it was called with."""

    def __init__(self):
        self.last_context = None

    def call(self, context):
        self.last_context = dict(context)
        return {
            "action": "respond",
            "content": "ok",
            "confidence": 1.0,
            "reasoning": "",
        }

    def is_available(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return "fake"


def _brain(tmp_path):
    llm = _CapturingLLM()
    memory = MemoryManager(
        episodic_db=str(tmp_path / "ep.db"),
        semantic_dir=str(tmp_path / "sem"),
    )
    return Brain(llm=llm, memory=memory), llm


def test_think_forwards_system_prompt_to_llm(tmp_path):
    brain, llm = _brain(tmp_path)
    brain.think(
        "привет",
        session_id="s1",
        system_prompt="ты — Аня, фрилансер",
    )
    assert llm.last_context is not None
    assert llm.last_context.get("system_prompt") == "ты — Аня, фрилансер"


def test_think_without_system_prompt_omits_the_key(tmp_path):
    brain, llm = _brain(tmp_path)
    brain.think("hi", session_id="s2")
    # When the persona isn't requested, the key should be absent so the
    # adapter uses its default reasoning-engine prompt.
    assert "system_prompt" not in llm.last_context


def test_empty_system_prompt_is_treated_as_no_override(tmp_path):
    brain, llm = _brain(tmp_path)
    brain.think("hi", session_id="s3", system_prompt="   ")
    assert "system_prompt" not in llm.last_context

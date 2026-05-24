"""
Integration tests: PII redaction across the Brain pipeline.

Verifies the full contract end-to-end:
    - ContextBuilder scrubs input, history, and facts before LLM sees them
    - Brain.think() restores tokens in `respond` / `clarify` output
    - Memory stores the user's RAW input (so restarts can still restore)
"""

from __future__ import annotations

from unittest.mock import MagicMock

from brain.context_builder import ContextBuilder
from brain.core import Brain
from brain.interfaces.llm_interface import LLMInterface
from brain.interfaces.memory_interface import MemoryInterface
from brain.privacy import PIIRedactor


# ────────────────────────────────────────────────────────────────────
# ContextBuilder
# ────────────────────────────────────────────────────────────────────

def test_context_builder_redacts_input_history_and_facts():
    memory = MagicMock(spec=MemoryInterface)
    memory.recall_history.return_value = [
        {"role": "user", "content": "I emailed alice@corp.com last week"},
        {"role": "assistant", "content": "Got it — alice@corp.com it is."},
    ]
    memory.recall_facts.return_value = [
        {"text": "Customer record: phone +7 495 123-45-67", "score": 0.9},
    ]

    redactor = PIIRedactor()
    builder = ContextBuilder(memory=memory, redactor=redactor)

    context = builder.build(
        raw_input="Send to bob@corp.com please",
        goals=[],
        session_id="sess-1",
    )

    # No raw PII leaks into the context dict
    blob = repr(context)
    assert "bob@corp.com" not in blob
    assert "alice@corp.com" not in blob
    assert "+7 495" not in blob and "123-45-67" not in blob

    # Tokens are present where the values used to be
    assert "[EMAIL_" in context["input"]
    assert any("[EMAIL_" in m["content"] for m in context["history"])
    assert any("[PHONE_RU_" in f["text"] for f in context["facts"])


def test_context_builder_without_redactor_passes_input_through():
    memory = MagicMock(spec=MemoryInterface)
    memory.recall_history.return_value = []
    memory.recall_facts.return_value = []

    builder = ContextBuilder(memory=memory)  # no redactor — backward compat
    context = builder.build(
        raw_input="Send to bob@corp.com",
        goals=[],
        session_id="s",
    )
    assert context["input"] == "Send to bob@corp.com"


# ────────────────────────────────────────────────────────────────────
# Brain.think — end-to-end
# ────────────────────────────────────────────────────────────────────

def test_brain_hides_pii_from_llm_and_restores_in_response():
    """
    The LLM must never see real email; the user must receive a real email
    back in the assistant's reply.
    """
    redactor = PIIRedactor()

    # Mock LLM — sees the context dict, "answers" by echoing the redacted
    # input. This lets us assert exactly what the LLM observed.
    seen_contexts: list[dict] = []

    def llm_call(context: dict) -> dict:
        seen_contexts.append(context)
        # Pretend the LLM crafted a response referencing the same token
        # it saw in the input — find any `[KIND_N]` token and echo it.
        import re as _re
        token_match = _re.search(r"\[[A-Z_]+_\d+\]", context["input"])
        token = token_match.group(0) if token_match else "(none)"
        return {
            "action": "respond",
            "content": f"Sure — I'll contact {token} today.",
            "confidence": 0.9,
            "reasoning": "echo",
        }

    llm = MagicMock(spec=LLMInterface)
    llm.call.side_effect = llm_call

    memory = MagicMock(spec=MemoryInterface)
    memory.recall_history.return_value = []
    memory.recall_facts.return_value = []

    brain = Brain(llm=llm, memory=memory, redactor=redactor)

    result = brain.think(
        "Please email bob@corp.com about the renewal",
        session_id="alice",
    )

    # 1. LLM saw redacted input — no raw email anywhere in its context
    assert len(seen_contexts) == 1
    ctx_blob = repr(seen_contexts[0])
    assert "bob@corp.com" not in ctx_blob
    assert "[EMAIL_1]" in seen_contexts[0]["input"]

    # 2. Response was restored before reaching the caller
    assert "bob@corp.com" in result.content
    assert "[EMAIL_1]" not in result.content


def test_brain_stores_raw_user_input_in_memory_not_token():
    """
    Memory must hold the real text so the redactor can be rebuilt after
    a process restart without losing the ability to restore.
    """
    redactor = PIIRedactor()
    llm = MagicMock(spec=LLMInterface)
    llm.call.return_value = {
        "action": "respond",
        "content": "ok",
        "confidence": 0.9,
        "reasoning": "ok",
    }
    memory = MagicMock(spec=MemoryInterface)
    memory.recall_history.return_value = []
    memory.recall_facts.return_value = []

    brain = Brain(llm=llm, memory=memory, redactor=redactor)
    brain.think("Mail to user@example.com", session_id="s")

    # First memory.store call is the user's input — must be raw
    user_stores = [c for c in memory.store.call_args_list if c.args[1] == "user"]
    assert user_stores, "expected at least one user-role store"
    raw_stored = user_stores[0].args[2]
    assert "user@example.com" in raw_stored
    assert "[EMAIL_" not in raw_stored


def test_brain_isolates_sessions():
    """One user's email must not appear in another user's context."""
    redactor = PIIRedactor()
    seen: list[dict] = []

    def llm_call(context: dict) -> dict:
        seen.append(context)
        return {
            "action": "respond",
            "content": "noted",
            "confidence": 0.9,
            "reasoning": "x",
        }

    llm = MagicMock(spec=LLMInterface)
    llm.call.side_effect = llm_call
    memory = MagicMock(spec=MemoryInterface)
    memory.recall_history.return_value = []
    memory.recall_facts.return_value = []

    brain = Brain(llm=llm, memory=memory, redactor=redactor)
    brain.think("Email alice@x.com", session_id="A")
    brain.think("Email bob@y.com", session_id="B")

    # Tokens are session-scoped → both start at _1 in their own session
    assert "[EMAIL_1]" in seen[0]["input"]
    assert "[EMAIL_1]" in seen[1]["input"]
    # And neither session leaks the other one's email
    assert "alice@x.com" not in seen[1]["input"]
    assert "bob@y.com" not in seen[0]["input"]

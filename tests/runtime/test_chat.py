"""Tests for runtime.chat.ChatHandler — routing + plain-text replies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from brain.skills.job import Job, JobStatus, JobStore
from runtime.chat import ChatHandler, ChatReply, _looks_like_job


# ════════════════════════════════════════════════════════════════════
# Tiny stub Brain
# ════════════════════════════════════════════════════════════════════

@dataclass
class _Think:
    action: str = "respond"
    content: Any = "hello"
    needs_human_approval: bool = False


class _StubBrain:
    """Minimal Brain stand-in: think() returns a canned ThinkResult-like obj."""

    def __init__(self, think_reply=None, intake_reply=None):
        self.think_reply = think_reply
        self.intake_reply = intake_reply
        self.think_calls = []
        self.intake_calls = []

    def think(self, raw_input, *, session_id, **kwargs):
        self.think_calls.append((raw_input, session_id, kwargs))
        return self.think_reply or _Think()

    def intake_job(self, job: Job):
        self.intake_calls.append(job)
        return self.intake_reply


# ════════════════════════════════════════════════════════════════════
# Conversation path
# ════════════════════════════════════════════════════════════════════

def test_chat_routes_short_message_through_brain_think():
    brain = _StubBrain(think_reply=_Think(action="respond", content="привет"))
    handler = ChatHandler(brain=brain)
    reply = handler.handle(brief="hi", client_id="alice")
    assert isinstance(reply, ChatReply)
    assert reply.text == "привет"
    assert reply.used_workflow is False
    assert reply.job_id is None
    assert len(brain.think_calls) == 1
    raw_input, session_id, kwargs = brain.think_calls[0]
    assert raw_input == "hi"
    assert session_id == "chat:alice"
    # Persona must be forwarded so the LLM has a conversational voice
    assert kwargs.get("system_prompt", "")
    assert "Аня" in kwargs["system_prompt"]


def test_chat_response_strips_brain_internals():
    """Reply must be plain text — no JSON, no ThinkResult fields."""
    brain = _StubBrain(think_reply=_Think(action="respond", content="  ok then  "))
    handler = ChatHandler(brain=brain)
    reply = handler.handle(brief="hi", client_id="alice")
    assert reply.text == "ok then"


def test_chat_clarify_action_is_treated_as_response():
    brain = _StubBrain(think_reply=_Think(action="clarify", content="что именно?"))
    reply = ChatHandler(brain=brain).handle(brief="?", client_id="alice")
    assert reply.text == "что именно?"


def test_chat_wait_action_yields_neutral_message():
    brain = _StubBrain(think_reply=_Think(action="wait", content=None))
    reply = ChatHandler(brain=brain).handle(brief="hmm", client_id="alice")
    assert reply.text
    # Must NOT leak the literal "wait" word — that's an internal action.
    assert "wait" not in reply.text.lower()


def test_chat_handles_empty_input_silently():
    """Empty input → empty reply.text → live loop sends nothing."""
    brain = _StubBrain()
    reply = ChatHandler(brain=brain).handle(brief="   ", client_id="alice")
    assert reply.text == ""
    assert brain.think_calls == []  # never bothered the LLM


def test_chat_recovers_when_brain_raises(caplog):
    class _ExplodingBrain:
        def think(self, *_a, **_k):
            raise RuntimeError("boom")
        def intake_job(self, _job):
            raise RuntimeError("boom too")
    reply = ChatHandler(brain=_ExplodingBrain()).handle(brief="hi", client_id="x")
    assert reply.text  # We answered, didn't crash
    assert reply.used_workflow is False


def test_session_ids_are_stable_and_safe(monkeypatch):
    brain = _StubBrain()
    handler = ChatHandler(brain=brain)
    handler.handle(brief="hi", client_id="telegram:123:alice")
    handler.handle(brief="hi again", client_id="telegram:123:alice")
    sessions = [call[1] for call in brain.think_calls]
    # same client → same session
    assert sessions[0] == sessions[1]
    # session id sanitized but client preserved as text
    assert "telegram" in sessions[0]


# ════════════════════════════════════════════════════════════════════
# Workflow path
# ════════════════════════════════════════════════════════════════════

class _Outcome:
    def __init__(self, status, deliverables=None, summary=""):
        self.final_status = status
        self.deliverables = deliverables or []
        self.job_id = "j1"
        class _R: pass
        self.verifier_report = _R()
        self.verifier_report.summary = summary
        self.notes = []


def test_attachment_routes_through_intake_job(tmp_path):
    store = JobStore(":memory:")
    brain = _StubBrain(intake_reply=_Outcome(JobStatus.DELIVERED, ["out.docx"]))
    handler = ChatHandler(brain=brain, job_store=store)
    reply = handler.handle(
        brief="see attached",
        client_id="bob@example.com",
        attachments=["/tmp/anything.docx"],
        source="email",
    )
    assert reply.used_workflow is True
    assert reply.deliverables == ["out.docx"]
    assert len(brain.intake_calls) == 1
    assert brain.intake_calls[0].attachments == ["/tmp/anything.docx"]
    assert brain.intake_calls[0].source == "email"


def test_intake_failure_yields_friendly_message():
    store = JobStore(":memory:")
    brain = _StubBrain(
        intake_reply=_Outcome(JobStatus.FAILED, summary="too many edits"),
    )
    handler = ChatHandler(brain=brain, job_store=store)
    reply = handler.handle(
        brief="edit this", client_id="alice",
        attachments=["/tmp/d.docx"],
    )
    assert reply.used_workflow is True
    assert "too many edits" in reply.text


def test_intake_decline_returns_polite_message():
    store = JobStore(":memory:")
    brain = _StubBrain(intake_reply=_Outcome(JobStatus.DECLINED))
    handler = ChatHandler(brain=brain, job_store=store)
    reply = handler.handle(
        brief="do thing",
        client_id="alice",
        attachments=["/tmp/x.docx"],
    )
    assert reply.used_workflow is True
    assert "пока" in reply.text or "опиши" in reply.text.lower()


# ════════════════════════════════════════════════════════════════════
# Heuristic
# ════════════════════════════════════════════════════════════════════

def test_job_keyword_routes_to_workflow_even_without_attachment():
    """An explicit 'edit this' style brief should be treated as a job."""
    brain = _StubBrain(
        intake_reply=_Outcome(JobStatus.DECLINED),
        think_reply=_Think(action="respond", content="should not appear"),
    )
    handler = ChatHandler(brain=brain, job_store=JobStore(":memory:"))
    reply = handler.handle(brief="edit this paragraph", client_id="alice")
    assert reply.used_workflow is True
    assert brain.think_calls == []


def test_plain_chat_does_not_route_to_workflow():
    brain = _StubBrain(think_reply=_Think(action="respond", content="hi"))
    handler = ChatHandler(brain=brain, job_store=JobStore(":memory:"))
    reply = handler.handle(brief="how are you?", client_id="alice")
    assert reply.used_workflow is False


# ════════════════════════════════════════════════════════════════════
# Slash commands
# ════════════════════════════════════════════════════════════════════

def test_slash_start_returns_welcome_without_calling_brain():
    brain = _StubBrain()
    reply = ChatHandler(brain=brain).handle(brief="/start", client_id="alice")
    assert "Аня" in reply.text or "фрилансер" in reply.text.lower()
    assert brain.think_calls == []
    assert brain.intake_calls == []


def test_slash_help_lists_capabilities():
    brain = _StubBrain()
    reply = ChatHandler(brain=brain).handle(brief="/help", client_id="alice")
    assert "/start" in reply.text
    assert "/help" in reply.text
    assert "/ping" in reply.text
    assert brain.think_calls == []


def test_slash_ping_returns_short_acknowledgement():
    brain = _StubBrain()
    reply = ChatHandler(brain=brain).handle(brief="/ping", client_id="alice")
    assert reply.text  # not empty
    assert len(reply.text) < 80
    assert brain.think_calls == []


def test_slash_command_with_bot_suffix_still_matches():
    """Telegram appends @botname to commands in group chats — must still work."""
    brain = _StubBrain()
    reply = ChatHandler(brain=brain).handle(
        brief="/help@my_freelancer_bot", client_id="alice",
    )
    assert "/start" in reply.text
    assert brain.think_calls == []


def test_unknown_slash_falls_through_to_chat():
    brain = _StubBrain(think_reply=_Think(content="не знаю такого"))
    reply = ChatHandler(brain=brain).handle(brief="/foobar", client_id="alice")
    assert reply.text == "не знаю такого"
    assert len(brain.think_calls) == 1


# ════════════════════════════════════════════════════════════════════
# Persona compatibility fallback
# ════════════════════════════════════════════════════════════════════

class _LegacyBrain:
    """Older Brain signature — doesn't accept the system_prompt kwarg."""

    def __init__(self):
        self.calls = 0

    def think(self, raw_input, *, session_id):
        self.calls += 1
        return _Think(content=f"echo: {raw_input}")


def test_chat_falls_back_when_brain_lacks_system_prompt():
    """Backwards-compatible: legacy Brain stubs without kwarg still work."""
    reply = ChatHandler(brain=_LegacyBrain()).handle(brief="hi", client_id="alice")
    assert reply.text == "echo: hi"


def test_looks_like_job_heuristic():
    assert _looks_like_job("Please edit this DOCX")
    assert _looks_like_job("переведи на русский")
    assert _looks_like_job("сделай слайды")
    assert not _looks_like_job("how are you today")
    assert not _looks_like_job("")


# ════════════════════════════════════════════════════════════════════
# Welcome book integration
# ════════════════════════════════════════════════════════════════════

def test_first_contact_prepends_welcome(tmp_path):
    from runtime.welcome_book import WelcomeBook
    brain = _StubBrain(think_reply=_Think(content="hi there"))
    handler = ChatHandler(
        brain=brain, welcome_book=WelcomeBook(tmp_path / "w.db"),
    )
    reply = handler.handle(brief="hello", client_id="alice")
    assert "hi there" in reply.text
    # Welcome prefix appears ONCE on first reply
    assert "Аня" in reply.text or "фрилансер" in reply.text.lower()


def test_returning_client_gets_no_welcome_prefix(tmp_path):
    from runtime.welcome_book import WelcomeBook
    brain = _StubBrain(think_reply=_Think(content="hi there"))
    handler = ChatHandler(
        brain=brain, welcome_book=WelcomeBook(tmp_path / "w.db"),
    )
    handler.handle(brief="hello", client_id="alice")  # first contact
    second = handler.handle(brief="hi again", client_id="alice")
    assert second.text == "hi there"  # no prefix


def test_welcome_only_applied_when_brain_reply_non_empty(tmp_path):
    """Empty input → empty reply → no welcome to prepend (silence stays silence)."""
    from runtime.welcome_book import WelcomeBook
    book = WelcomeBook(tmp_path / "w.db")
    brain = _StubBrain()
    handler = ChatHandler(brain=brain, welcome_book=book)
    reply = handler.handle(brief="   ", client_id="alice")
    assert reply.text == ""
    # First message wasn't a real contact (we sent nothing) so book is still empty
    assert book.has_greeted("alice") is False


def test_welcome_book_failure_does_not_block_reply(tmp_path):
    class _BrokenBook:
        def mark_greeted(self, _): raise RuntimeError("disk full")
    brain = _StubBrain(think_reply=_Think(content="hi"))
    reply = ChatHandler(brain=brain, welcome_book=_BrokenBook()).handle(
        brief="hello", client_id="alice",
    )
    assert reply.text == "hi"


def test_no_welcome_book_means_no_prefix():
    brain = _StubBrain(think_reply=_Think(content="hi"))
    reply = ChatHandler(brain=brain).handle(brief="hello", client_id="alice")
    assert reply.text == "hi"

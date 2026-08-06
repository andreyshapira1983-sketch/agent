"""When the answer-safety check breaks, the unverified draft must not go out.

Census item A2, decided by measurement rather than by taste. Reproduced on a
draft the low-evidence policy really truncates:

    healthy : 1291 chars -> 460, and the answer opened
              "Conclusion: no claim could be backed by the sources gathered
               this cycle."
    broken  : 1291 chars unchanged, and the answer opened
              "Conclusion: the API returns 42 on every call and has done since
               2019 [general-knowledge]."

Three separate defects came out of that, each provable on its own: the
`answer_enforcement` event disappeared, so "ran and changed nothing" and "never
ran" looked identical; no defect signal was recorded, so the episode banked as
an ordinary success; and the user received a confident factual claim the
evidence did not support, with nothing anywhere to say so.

That rules out returning the original draft. It is exactly the text enforcement
existed to remove — handing it over because the remover crashed delivers the
harm the mechanism was built to prevent. So the run fails closed on CONTENT
without taking the cycle down: a deterministic refusal, an explicit failure
event, a defect signal, and no clean success.

The handler covers SIX operations, which is why this file is parameterised over
all of them. Fixing the exception in `apply_answer_enforcement` alone would
leave `draft.set_body` free to keep turning a break into a success — and
`set_body` is the one that makes the recovery path subtle, since writing the
refusal through it would be calling the mechanism that just failed.
"""
from __future__ import annotations

import pytest

from core.loop_response_deciders import (
    ENFORCEMENT_FAILURE_ANSWER,
    AgentLoopResponseDeciders,
    EnforcementFallbackUnavailable,
)
from core.smart_memory import decide_usage_eligibility, episode_from_agent_cycle

#: The claim the healthy path removes. Its absence from the delivered text is
#: the whole point, so it is named once and checked everywhere.
UNSUPPORTED_CLAIM = "the API returns 42 on every call"

DRAFT = (
    f"Conclusion: {UNSUPPORTED_CLAIM} and has done since 2019 "
    "[general-knowledge].\n"
    "Facts: " + "the endpoint is stable and documented in three places. " * 20
    + "[general-knowledge]\n"
    "Sources: general knowledge\nConfidence: high\n"
    "Unverified: nothing\nSafety: ok"
)


class _Log:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def log(self, name, payload=None, **kw):
        self.events.append((name, payload if isinstance(payload, dict) else {}))

    def names(self) -> list[str]:
        return [n for n, _ in self.events]

    def payload(self, name: str) -> dict:
        return next(p for n, p in self.events if n == name)


class _Report:
    """Ten unverified chunks — above the policy's eight-chunk floor."""

    chain_was_empty = False
    total_chunks = 10
    verified_chunks = 0
    dialogue_supported_chunks = 0
    unverified_chunks = 10
    cited_but_unmatched_chunks = 0
    topic_supported_but_claim_unverified_chunks = 0
    subagent_asserted_chunks = 0
    user_asserted_chunks = 0
    self_declared_chunks = 0
    chunks = ()
    malformed_output = False


class _Ranking:
    realtime_required = True
    ranks = ()


class _Role:
    role = "researcher"


class _Agent(AgentLoopResponseDeciders):
    def __init__(self) -> None:
        self.log = _Log()
        self.persistent_store = None
        self.clarification_gate_enabled = False
        self.last_verification = _Report()
        self.last_provenance = None
        self.last_self_analysis = None
        self.last_source_ranking = _Ranking()
        self.last_role_context = _Role()
        self._defect_signals: list[str] = []

    def _durable_learning_suppressed(self, sink):
        return True

    def _sensor_failed(self, *a):
        self.log.log("sensor_failed", {})

    def build(self):
        return self._build_response_draft(
            DRAFT,
            user_question="what does the API return?",
            artifacts={},
            replan_exhausted=False,
            local_critique_active=False,
            verifier_failure=False,
        )


# ---------------------------------------------------------------------------
# The healthy path, so the contract below is measured against something real
# ---------------------------------------------------------------------------

def test_the_healthy_path_really_truncates():
    """Without this the rest of the file would be testing an inert case.

    The first attempt at this investigation used three chunks and never
    triggered, which is why the user-visible damage went unproven for a while.
    The floor is eight (`core/low_evidence_policy.py`).
    """
    agent = _Agent()
    body = agent.build().render()

    assert UNSUPPORTED_CLAIM not in body
    assert len(body) < len(DRAFT)
    assert "answer_enforcement" in agent.log.names()
    assert "low_evidence_truncation" in agent.log.names()
    assert agent._defect_signals == []


# ---------------------------------------------------------------------------
# The same contract at every one of the six failure points
# ---------------------------------------------------------------------------

#: (stage, what to break). The stage name is what the failure event reports, so
#: an operator reading the journal learns WHICH step broke, not merely that one
#: did.
#: `read_state` is deliberately absent. Its two reads —
#: `self.last_source_ranking` and `self.last_verification` — happen EARLIER in
#: `_build_response_draft`, in the verification-summary and causal-credit
#: deciders, so an object that raises on them never reaches the enforcement
#: handler at all. The stage name stays in the code because it is what the
#: journal should report if those reads ever move; it simply cannot be provoked
#: from outside today, and pretending otherwise with a contrived break would
#: test the test rather than the contract.
STAGES = [
    ("evidence_expected", "core.loop_response_deciders.is_evidence_expected"),
    ("apply_enforcement", "core.loop_response_deciders.apply_answer_enforcement"),
    ("log_enforcement", "log:answer_enforcement"),
    ("log_truncation", "log:low_evidence_truncation"),
    ("set_body", "set_body"),
]


def _break(agent: _Agent, target: str, monkeypatch):
    """Make exactly one of the six operations raise."""
    if target == "set_body":
        import core.response_draft as rd

        def _boom(self, *a, **kw):
            raise RuntimeError("boom: set_body")
        monkeypatch.setattr(rd.ResponseDraft, "set_body", _boom)
    elif target.startswith("log:"):
        wanted = target.split(":", 1)[1]
        original = agent.log.log

        def _boom_log(name, payload=None, **kw):
            if name == wanted:
                raise RuntimeError(f"boom: logging {wanted}")
            return original(name, payload, **kw)
        agent.log.log = _boom_log  # type: ignore[method-assign]
    else:
        monkeypatch.setattr(target, lambda *a, **kw: (_ for _ in ()).throw(
            RuntimeError("boom: " + target)))


@pytest.mark.parametrize(("stage", "target"), STAGES, ids=[s for s, _ in STAGES])
def test_every_failure_point_withholds_the_unverified_draft(stage, target, monkeypatch):
    agent = _Agent()
    _break(agent, target, monkeypatch)

    body = agent.build().render()

    # 1. The claim enforcement would have removed never reaches the user.
    assert UNSUPPORTED_CLAIM not in body, f"{stage}: the unverified draft went out"
    # 2. What arrives is the deterministic refusal, not a partial rewrite.
    assert body == ENFORCEMENT_FAILURE_ANSWER, stage
    # 3. The failure is named, with the stage that broke.
    assert "answer_enforcement_failed" in agent.log.names(), stage
    payload = agent.log.payload("answer_enforcement_failed")
    assert payload["stage"] == stage
    assert payload["exception_type"] == "RuntimeError"
    assert payload["fallback_applied"] is True
    assert payload["original_withheld"] is True
    # 4. The episode carries the defect.
    assert "answer_enforcement_failed" in agent._defect_signals, stage
    # 5. Nothing pretends the check succeeded. `answer_enforcement` may have
    #    been written before the break at a later stage — what must never
    #    appear is `low_evidence_truncation` claiming a truncation that did not
    #    reach the user.
    names = agent.log.names()
    if "low_evidence_truncation" in names:
        assert stage == "set_body", (
            f"{stage}: a truncation was announced but never delivered"
        )


# ---------------------------------------------------------------------------
# The episode contract
# ---------------------------------------------------------------------------

def test_the_signal_denies_a_clean_success():
    episode = episode_from_agent_cycle(
        goal="g", question="q", answer="a",
        tools_used=["file_read"], source_labels=["file:x"],
        verified_chunks=3, unverified_chunks=0,
        declared_completion="achieved",
        defect_signals=["answer_enforcement_failed"],
    )

    assert episode.completion_state == "partially_achieved"
    assert episode.completion_override == "answer_enforcement_failed"
    assert episode.declared_completion == "achieved", "the claim is refuted, not erased"
    assert decide_usage_eligibility(episode) is False


def test_an_honest_report_is_not_made_worse():
    """One direction only: it lowers a claim, it never punishes candour."""
    episode = episode_from_agent_cycle(
        goal="g", question="q", answer="a",
        tools_used=[], source_labels=[],
        declared_completion="blocked",
        defect_signals=["answer_enforcement_failed"],
    )

    assert episode.completion_state == "blocked"
    assert episode.completion_override is None


# ---------------------------------------------------------------------------
# The seventh case: the recovery path breaks too
# ---------------------------------------------------------------------------

def test_a_broken_fallback_still_never_returns_the_original(monkeypatch):
    """Controlled failure beats delivering the text the check meant to remove.

    This is the recursion the design exists to avoid: `set_body` breaks, the
    handler reaches for a replacement, that breaks as well — and the tempting
    answer, "return what we had", is precisely the confident unsupported claim
    the measurement caught going out.
    """
    import core.loop_response_deciders as mod

    agent = _Agent()
    monkeypatch.setattr(
        mod, "apply_answer_enforcement",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    # Only the FALLBACK construction breaks. `_build_response_draft` builds a
    # draft of its own at the top, and breaking that would test a different
    # failure entirely.
    real = mod.ResponseDraft

    def _only_the_fallback_breaks(*a, **kw):
        if kw.get("body") == ENFORCEMENT_FAILURE_ANSWER:
            raise RuntimeError("boom: fallback")
        return real(*a, **kw)

    monkeypatch.setattr(mod, "ResponseDraft", _only_the_fallback_breaks)

    with pytest.raises(EnforcementFallbackUnavailable):
        agent.build()

    # The failure was still announced before giving up.
    assert "answer_enforcement_failed" in agent.log.names()
    assert "answer_enforcement_failed" in agent._defect_signals

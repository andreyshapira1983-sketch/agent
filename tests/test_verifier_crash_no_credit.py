"""A crashed verifier must not be able to mint or promote a procedure.

The soft-fail path keeps the draft and records `verified=0, unverified=0`
(`core/loop.py:1635`). Those counts fall through every branch of the outcome
derivation to `success`, so a cycle whose verifier threw looks — to the
procedural layer — exactly like a cleanly verified one. `usage_eligible` does
not guard this: the procedural path never reads it. So a crash could create a
procedure and raise its confidence on evidence that was never measured.

This is the narrow half of MIR-057 and is deliberately fixed on its own, ahead
of the completion gates: it is a defect in the evidence axis itself, not a
consequence of conflating completion with it.

Only the procedural action changes. The episode is still banked, its `outcome`
is untouched, and nothing about the verifier is persisted — the crash is a
fact about one cycle, not a property of the episode.

The debit direction stays open on purpose. A structural failure (an abort, an
exhausted replan) is a fact the verifier has no part in, so suppressing that
debit would let a crash protect a genuinely bad procedure.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.bootstrap import build_agent
from core.loop import AgentLoop
from core.smart_memory import ProcedureRecord


@pytest.fixture(autouse=True)
def _offline_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENT_ALLOW_MOCK_ROUTING", "1")


def _agent(workspace: Path) -> AgentLoop:
    return build_agent(workspace, with_memory=True, approval_provider=None)


def _bank(agent: AgentLoop, *, verifier_failure: bool, verified: int = 0,
          unverified: int = 0, replan_exhausted: bool = False,
          tools: list[str] | None = None) -> None:
    """Bank one cycle the way the loop does, with the crash flag it carries."""
    agent._record_experience_memory(
        goal_description="read the file",
        question="сколько строк в файле core/loop_methods2.py",
        answer="Conclusion: 120 строк.",
        tools_used=tools if tools is not None else ["file_read"],
        source_labels=["file:core/loop_methods2.py"],
        verified_chunks=verified,
        unverified_chunks=unverified,
        replan_exhausted=replan_exhausted,
        verifier_failure=verifier_failure,
    )


def _procedures(agent: AgentLoop) -> list[ProcedureRecord]:
    return agent.procedural_store.load()


def _seed_procedure(agent: AgentLoop, *, key: str = "tools:file_read") -> ProcedureRecord:
    proc = ProcedureRecord(
        name="Workflow using file_read", workflow_key=key,
        trigger_tags=("file_read",), steps=("Run tool: file_read",),
        source_episode_ids=(), success_count=2, failure_count=0,
        confidence=0.75, status="active",
    )
    agent.procedural_store.rewrite([proc])
    return proc


# ==========================================================================
# The crash must not create, and must not credit.
# ==========================================================================
def test_a_crashed_verifier_does_not_mint_a_procedure(tmp_path: Path) -> None:
    """0/0 reads as `success`; without this guard the crash creates a workflow."""
    agent = _agent(tmp_path)
    assert _procedures(agent) == []

    _bank(agent, verifier_failure=True)

    assert _procedures(agent) == [], (
        "a verifier that threw measured nothing — there is no evidence a "
        "workflow worked, only an absence of contradiction"
    )


def test_a_crashed_verifier_does_not_raise_an_existing_procedure(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    before = _seed_procedure(agent)

    _bank(agent, verifier_failure=True)

    after = _procedures(agent)[0]
    assert after.success_count == before.success_count
    assert after.confidence == before.confidence
    assert after.status == before.status


def test_a_structural_debit_still_lands_when_the_verifier_crashed(tmp_path: Path) -> None:
    """A crash is no defence for a procedure that failed structurally."""
    agent = _agent(tmp_path)
    before = _seed_procedure(agent)
    agent._last_procedure_records = [before]
    agent._executed_tools = ["file_read"]

    _bank(agent, verifier_failure=True, replan_exhausted=True)

    after = _procedures(agent)[0]
    assert after.failure_count == before.failure_count + 1, (
        "replan exhaustion is a fact about the run that the verifier had no "
        "part in; letting a crash suppress it would shield a bad procedure"
    )
    assert after.success_count == before.success_count


# ==========================================================================
# Control: a healthy verifier keeps every prior behaviour.
# ==========================================================================
def test_a_healthy_verifier_still_mints_a_procedure(tmp_path: Path) -> None:
    agent = _agent(tmp_path)

    _bank(agent, verifier_failure=False, verified=3)

    procedures = _procedures(agent)
    assert len(procedures) == 1
    assert procedures[0].workflow_key == "tools:file_read"


def test_a_healthy_verifier_still_credits(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    before = _seed_procedure(agent)

    _bank(agent, verifier_failure=False, verified=3)

    after = _procedures(agent)[0]
    assert after.success_count == before.success_count + 1
    assert after.confidence > before.confidence


# ==========================================================================
# Blast radius: only the procedural action moves.
# ==========================================================================
def test_the_episode_is_still_banked_with_its_outcome_untouched(tmp_path: Path) -> None:
    agent = _agent(tmp_path)

    _bank(agent, verifier_failure=True)

    episodes = agent.episodic_store.load()
    assert len(episodes) == 1, "the crash suppresses procedural credit, not memory"
    assert episodes[0].outcome == "success", (
        "the evidence axis is not rewritten here — 0/0 still reads as it did. "
        "Fixing that encoding is a separate change"
    )


def test_nothing_about_the_verifier_is_persisted(tmp_path: Path) -> None:
    """The crash is a fact about one cycle, not a property of the episode."""
    agent = _agent(tmp_path)

    _bank(agent, verifier_failure=True)

    payload = agent.episodic_store.load()[0].to_dict()
    assert not [k for k in payload if "verifier" in k.lower()]


def test_credit_suppressed_is_false_when_a_debit_was_what_happened(tmp_path: Path) -> None:
    """The flag marks a downgrade, not merely "the crash flag was set".

    A crashed verifier on a structurally failed run still debits. Reporting
    that as a suppressed credit would describe an event that did not occur and
    would make the two cases indistinguishable in the log.
    """
    agent = _agent(tmp_path)
    proc = _seed_procedure(agent)
    agent._last_procedure_records = [proc]
    agent._executed_tools = ["file_read"]

    _bank(agent, verifier_failure=True, replan_exhausted=True)

    import json
    events = [
        json.loads(line)
        for line in Path(agent.log.path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    payload = [e for e in events if e.get("event") == "procedure_feedback"][-1]["payload"]
    assert payload["verdict"] == "failure"
    assert payload["credit_suppressed"] is False
    assert payload["applied"] == 1


# ==========================================================================
# Both soft-fail sites, exercised through the real loop rather than by
# handing the flag straight to the banking function.
# ==========================================================================
def _crashing_verify(*_args, **_kwargs):
    raise RuntimeError("verifier exploded")


def _feedback_payload(agent: AgentLoop) -> dict:
    import json
    events = [
        json.loads(line)
        for line in Path(agent.log.path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    payloads = [e["payload"] for e in events if e.get("event") == "procedure_feedback"]
    assert payloads, "the cycle never reached procedural feedback"
    return payloads[-1]


def test_the_initial_verify_crash_reaches_banking(tmp_path: Path, monkeypatch) -> None:
    """`core/loop.py:1625` — the flag has to survive the whole cycle.

    Asserting on the suppression flag rather than on a counter: in a mocked
    cycle nothing is attributed anyway, so an unchanged `success_count` would
    hold with or without the fix and would prove nothing.
    """
    agent = _agent(tmp_path)
    _seed_procedure(agent)
    monkeypatch.setattr("core.verifier.verify", _crashing_verify)

    agent.run("сколько строк в файле core/loop_methods2.py")

    assert _feedback_payload(agent)["credit_suppressed"] is True
    assert agent.procedural_store.load()[0].success_count == 2


def test_the_replan_verify_crash_reaches_banking(tmp_path: Path, monkeypatch) -> None:
    """`core/loop.py:1929` — the second soft-fail site, entered via the
    unresolved-URL branch, must set the same flag the first one does."""
    agent = _agent(tmp_path)
    _seed_procedure(agent)

    calls = {"verify": 0, "urls": 0}

    def _verify_then_crash(*_args, **kwargs):
        calls["verify"] += 1
        if calls["verify"] == 1:
            # A clean first pass, so the cycle would bank `success` and credit.
            # Unlike the initial site, the replan site does NOT replace the
            # report on crash — it keeps this one — so the counts here decide
            # the outcome, and they must be the ones that expose the defect.
            from core.verifier_models import VerificationReport
            return VerificationReport(
                total_chunks=1, verified_chunks=1, unverified_chunks=0,
                cited_but_unmatched_chunks=0, self_declared_chunks=0,
                structural_chunks=0, chunks=(),
                annotated_answer=kwargs.get("answer", ""),
                fully_unverified=False, chain_was_empty=False,
            )
        raise RuntimeError("verifier exploded on re-verify")

    def _one_unresolved_url(_report):
        calls["urls"] += 1
        return ["https://example.invalid/doc"] if calls["urls"] == 1 else []

    monkeypatch.setattr("core.verifier.verify", _verify_then_crash)
    monkeypatch.setattr("core.verifier.extract_unresolved_web_urls", _one_unresolved_url)

    agent.run("сколько строк в файле core/loop_methods2.py")

    assert calls["verify"] >= 2, "the replan branch was never entered"
    import json
    phases = [
        json.loads(line)["payload"].get("phase")
        for line in Path(agent.log.path).read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("event") == "verifier_failure"
    ]
    assert "verify_replan" in phases, (
        f"the crash must come from the replan site, not the initial one; saw {phases}"
    )
    assert _feedback_payload(agent)["credit_suppressed"] is True, (
        "a crash on re-verify must suppress credit exactly as the first site does"
    )
    assert agent.procedural_store.load()[0].success_count == 2


def test_the_suppression_is_reported_not_silent(tmp_path: Path) -> None:
    import json

    agent = _agent(tmp_path)
    _seed_procedure(agent)
    _bank(agent, verifier_failure=True)

    events = [
        json.loads(line)
        for line in Path(agent.log.path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    feedback = [e for e in events if e.get("event") == "procedure_feedback"]
    assert feedback, "procedure_feedback must still be logged"
    assert feedback[-1]["payload"].get("credit_suppressed") is True, (
        "a suppressed credit that looks like an absent one cannot be audited"
    )

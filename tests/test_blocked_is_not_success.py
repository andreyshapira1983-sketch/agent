"""A self-declared non-delivery cannot be banked as a success.

Measured on the live agent, 2026-08-02. Given a multi-step experiment to run,
the agent read documentation instead, answered honestly — "the experiment was
not performed" — and declared `blocked`. Episodic memory recorded
`outcome=success`. Three layers then told three different stories:

    the request      -> run a full experiment
    the answer       -> not performed, blocked
    episodic memory  -> success

`outcome` was derived from chunk counts alone, so a *well-cited* non-delivery
counted as well-cited. `usage_eligible=False` does not repair it: the row still
READS as a success to anyone (or anything) scanning history.

The rule pinned here: a declaration in which the run states it did not deliver
is an admission, not a verdict about evidence quality, and no chunk count may
overturn it.
"""
from __future__ import annotations

from core.smart_memory import episode_from_agent_cycle, procedure_credit_allowed


def _episode(**over):
    base = dict(
        goal="run the experiment",
        question="run a full controlled learning experiment",
        answer="Conclusion: the experiment was NOT performed. [file:docs/x.md]",
        tools_used=["file_read"],
        source_labels=["file:docs/x.md"],
        verified_chunks=3,      # a perfectly cited non-delivery
        unverified_chunks=0,
    )
    base.update(over)
    return episode_from_agent_cycle(**base)


class TestNonDeliveryIsNotSuccess:
    def test_blocked_is_not_banked_as_success(self):
        """The measured case, locked."""
        ep = _episode(declared_completion="blocked")
        assert ep.completion_state == "blocked"
        assert ep.outcome != "success", (
            "a run that declared it was blocked was banked as a success — "
            "memory would read it as a working precedent"
        )
        assert ep.outcome == "partial"

    def test_refused_is_not_banked_as_success(self):
        ep = _episode(declared_completion="refused")
        assert ep.outcome == "partial"

    def test_declared_failed_banks_failed(self):
        """A declared failure is not softened to `partial` either."""
        ep = _episode(declared_completion="failed")
        assert ep.outcome == "failed"

    def test_good_citations_cannot_overturn_the_admission(self):
        """Twenty verified chunks do not make a non-delivery a delivery: the
        counters judge evidence quality, not whether the task was done."""
        ep = _episode(declared_completion="blocked", verified_chunks=20)
        assert ep.outcome == "partial"


class TestDeliveryPathsAreUnchanged:
    def test_achieved_with_verified_evidence_still_succeeds(self):
        ep = _episode(declared_completion="achieved")
        assert ep.outcome == "success"

    def test_partially_achieved_is_still_a_delivery(self):
        """Partial delivery IS delivery of a part; the chunk counts stay the
        judge of its quality, exactly as before."""
        ep = _episode(declared_completion="partially_achieved")
        assert ep.outcome == "success"
        weak = _episode(
            declared_completion="partially_achieved",
            verified_chunks=1,
            unverified_chunks=5,
        )
        assert weak.outcome == "partial"  # decided by the counters, not the label

    def test_an_undeclared_run_is_judged_by_its_evidence(self):
        """No declaration (legacy or a synthesizer that emitted no marker):
        behaviour is untouched — the counters decide."""
        assert _episode(declared_completion=None).outcome == "success"
        assert _episode(
            declared_completion=None, verified_chunks=0, unverified_chunks=4
        ).outcome == "partial"

    def test_an_aborted_run_still_outranks_the_declaration(self):
        """`aborted_reason` is decided first and stays first."""
        ep = _episode(declared_completion="achieved", aborted_reason="budget")
        assert ep.outcome == "failed"


class TestCreditConsequences:
    def test_a_blocked_run_earns_no_procedure_credit(self):
        """The point of the fix: a non-delivery must not become a precedent."""
        ep = _episode(declared_completion="blocked")
        assert procedure_credit_allowed(ep) is False

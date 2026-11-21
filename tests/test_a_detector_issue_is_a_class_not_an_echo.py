"""Detector-minted issues merge by signal class — MIR-035's real root.

WHY THIS EXISTS. Measured 2026-08-22: the self-improvement registry held 106
issues, every one `open`, every one the same generic action — and the largest
families were the SAME detector signal echoing different campaign questions
(13× «detectors reasoning_action_mismatch, user_contract_unrepresented:
Campaign goal: …»). Two roots:

* the failure text embeds the full question, and the fingerprint hashes the
  text — so one detector class on N questions minted N permanent open issues;
* `reasoning_action_mismatch` feeds the route at all. MIR-015 measured that
  sensor at a 71% firing rate dominated by its own table defects, and its
  escalation-contract default says no enforcement ever attaches to this
  family — minting durable open defects from its firings IS acting on it.

THE RULE. A detector-minted issue is identified by its SIGNAL CLASS: the same
signals on different questions merge into one issue whose evidence
accumulates (the repair ladder repairs classes, not turns). And the
mismatch sensor's solo firings mint nothing — its verdicts are measured
unreliable; the verifier-caught signals (`content_refuted`,
`citation_fabricated`) stay first-class, because a fabricated citation that
nothing durable recorded was the route's founding case (2026-08-14).

WHAT THIS DOES NOT TOUCH. Non-detector failures (self-apply rollbacks, split
failures) keep text-keyed identity — two different rollback reasons ARE two
defects. The duplicate-mixin special case stays. Resolution paths
(`:self-issue-verify`, matching `committed_local`) are unchanged.
"""
from __future__ import annotations


def test_a_verifier_caught_signal_still_mints() -> None:
    """The route's founding case must survive: the agent invented four
    sources, `citation_fabricated` caught it, and nothing durable recorded it."""
    from core.self_build_memory import _detector_failure_text

    text = _detector_failure_text(["citation_fabricated"], "q: s")
    assert text is not None and "citation_fabricated" in text

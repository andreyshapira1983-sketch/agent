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

from core.self_improvement_issues import (
    failure_fingerprint,
    issue_from_failure,
)

_Q1 = "detectors content_refuted: Campaign goal: изучи фреймворк langgraph: ответ опровергнут"
_Q2 = "detectors content_refuted: «Назови один урок из прошлого опыта»: ответ опровергнут содержимым"


def test_the_same_detector_class_on_two_questions_is_one_issue() -> None:
    """The witness for the 106-row monoculture: identity is the class."""
    assert failure_fingerprint(_Q1) == failure_fingerprint(_Q2), (
        "one detector class on two questions minted two permanent issues — "
        "the registry grows by one open row per campaign turn forever"
    )


def test_different_detector_classes_stay_distinct() -> None:
    a = "detectors content_refuted: q"
    b = "detectors citation_fabricated: q"
    assert failure_fingerprint(a) != failure_fingerprint(b)


def test_a_detector_issue_carries_a_class_action_and_title() -> None:
    """Distinct from the generic bucket, so `suppress_generic_issue_duplicates`
    and consumers can tell an investigated signal from an unclassified one."""
    issue = issue_from_failure(_Q1, "2026-08-22T00:00:00+00:00")
    assert issue.action == "investigate_detector_signal"
    assert "content_refuted" in issue.title


def test_the_mismatch_sensors_solo_firing_mints_nothing() -> None:
    """MIR-015's contract default applied at this sink: a sensor firing on 71%
    of turns, dominated by its own table defects, must not create permanent
    open defects. Solo (or with its co-lexical twin) it is dropped."""
    from core.self_build_memory import _detector_failure_text

    assert _detector_failure_text(
        ["reasoning_action_mismatch"], "q: s"
    ) is None
    assert _detector_failure_text(
        ["reasoning_action_mismatch", "user_contract_unrepresented"], "q: s"
    ) is None


def test_a_verifier_caught_signal_still_mints() -> None:
    """The route's founding case must survive: the agent invented four
    sources, `citation_fabricated` caught it, and nothing durable recorded it."""
    from core.self_build_memory import _detector_failure_text

    text = _detector_failure_text(["citation_fabricated"], "q: s")
    assert text is not None and "citation_fabricated" in text


def test_a_mixed_firing_keeps_only_the_reliable_signals() -> None:
    from core.self_build_memory import _detector_failure_text

    text = _detector_failure_text(
        ["reasoning_action_mismatch", "content_refuted"], "q: s"
    )
    assert text is not None
    assert "content_refuted" in text
    assert "reasoning_action_mismatch" not in text, (
        "the unreliable sensor rides into the durable record on a reliable "
        "signal's coat-tails"
    )


def test_non_detector_failures_keep_text_keyed_identity() -> None:
    """The boundary: two different rollback reasons ARE two defects."""
    a = failure_fingerprint("self-apply rolled_back: targeted tests failed")
    b = failure_fingerprint("self-apply rolled_back: anatomy map not updated")
    assert a != b

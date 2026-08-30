# Run: "C:\Users\andre\AppData\Local\Programs\Python\Python311\python.exe" -m pytest tests/test_the_operator_can_retire_an_issue.py
"""Red witness: retire_issue does not exist yet in core.self_improvement_issues.

Four checks, one per behaviour point:
  1. open issue with matching fingerprint -> resolved, evidence gains
     'retired-by-operator|<time>|<reason>', returns True.
  2. no such fingerprint -> nothing changes, returns False.
  3. empty reason (after strip) -> refusal, status stays open, returns False.
  4. already resolved -> returns False, evidence untouched (no repeated verdicts).

All data on tmp_path; never write to live data/.
"""


from core.self_improvement_issues import (
    SelfImprovementIssueRegistry,
    retire_issue,
)


def _make_open_issue(registry, text="boom", observed_at="2026-08-30T00:00:00Z"):
    """Create an open issue and return its fingerprint."""
    issue = registry.upsert_failure(text, observed_at)
    return issue.fingerprint


def test_retire_open_issue_marks_resolved_and_appends_verdict(tmp_path):
    registry = SelfImprovementIssueRegistry(tmp_path / "issues.jsonl")
    fp = _make_open_issue(registry)

    result = retire_issue(registry, fp, reason="fixed by operator")

    assert result is True
    issue = registry.list()[0]
    assert issue.status == "resolved"
    assert any(
        line.startswith("retired-by-operator|") and line.endswith("|fixed by operator")
        for line in issue.evidence
    )


def test_retire_unknown_fingerprint_changes_nothing(tmp_path):
    registry = SelfImprovementIssueRegistry(tmp_path / "issues.jsonl")
    _make_open_issue(registry)

    result = retire_issue(registry, "no-such-fingerprint", reason="whatever")

    assert result is False
    issue = registry.list()[0]
    assert issue.status == "open"
    assert not any(line.startswith("retired-by-operator|") for line in issue.evidence)


def test_retire_with_empty_reason_is_refused(tmp_path):
    registry = SelfImprovementIssueRegistry(tmp_path / "issues.jsonl")
    fp = _make_open_issue(registry)

    result = retire_issue(registry, fp, reason="   ")

    assert result is False
    issue = registry.list()[0]
    assert issue.status == "open"
    assert not any(line.startswith("retired-by-operator|") for line in issue.evidence)


def test_retire_already_resolved_returns_false_and_keeps_evidence(tmp_path):
    registry = SelfImprovementIssueRegistry(tmp_path / "issues.jsonl")
    fp = _make_open_issue(registry)

    assert retire_issue(registry, fp, reason="first verdict") is True
    evidence_before = registry.list()[0].evidence

    result = retire_issue(registry, fp, reason="second verdict")

    assert result is False
    issue = registry.list()[0]
    assert issue.status == "resolved"
    assert issue.evidence == evidence_before
    assert sum(
        1 for line in issue.evidence if line.startswith("retired-by-operator|")
    ) == 1

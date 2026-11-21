"""Tests for core.instruction_conflict_gate.

The four scenarios in the operator's exam are pinned here as named tests, so a
regression in the turnstile shows up as a failing scenario rather than as a
quietly-applied reviewer suggestion.
"""
from __future__ import annotations

from core.instruction_conflict_gate import (
    FORBIDDEN_WHILE_CONFLICTED,
    Directive,
    evaluate,
    reviewer_vs_contract,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _contract(subject: str, demand: str, **kw) -> Directive:
    return Directive(
        source_level="task_contract",
        source_name=kw.pop("source", "спецификация задачи"),
        subject=subject,
        demand=demand,
        **kw,
    )


def _advisor(subject: str, demand: str, **kw) -> Directive:
    return Directive(
        source_level="advisor",
        source_name=kw.pop("source", "код-ревью"),
        subject=subject,
        demand=demand,
        **kw,
    )


# ---------------------------------------------------------------------------
# The safe direction: no conflict must never block
# ---------------------------------------------------------------------------

def test_no_directives_proceeds():
    outcome = evaluate([])
    assert outcome.mode == "proceed"
    assert not outcome.is_blocked
    assert outcome.forbidden_actions == ()
    assert outcome.report() == ""


def test_same_demand_restated_is_not_a_conflict():
    outcome = evaluate([
        _contract("порядок вывода", "Стабильный порядок."),
        _advisor("порядок вывода", "стабильный порядок"),
    ])
    assert outcome.mode == "proceed", (
        "the same requirement in different casing/punctuation must not be "
        "reported as a contradiction"
    )


def test_nothing_is_forbidden_while_proceeding():
    outcome = evaluate([_contract("порядок вывода", "стабильный порядок")])
    for action in FORBIDDEN_WHILE_CONFLICTED:
        assert not outcome.is_forbidden(action)


# ---------------------------------------------------------------------------
# Turnstile behaviour
# ---------------------------------------------------------------------------


def test_reading_is_not_forbidden():
    """The gate stops changes, not thinking — reads stay available."""
    outcome = reviewer_vs_contract(
        subject="порядок элементов",
        contract_demand="стабильный порядок",
        reviewer_demand="сортировать по имени",
    )
    for action in ("read_file", "run_tests", "ask_operator"):
        assert not outcome.is_forbidden(action), action


# ---------------------------------------------------------------------------
# Authority ranking
# ---------------------------------------------------------------------------


def test_same_level_conflict_has_no_automatic_priority():
    outcome = evaluate([
        _advisor("порядок элементов", "сортировать по имени", source="ревьюер A"),
        _advisor("порядок элементов", "стабильный порядок", source="ревьюер B"),
    ])
    assert outcome.is_blocked
    finding = outcome.findings[0]
    assert finding.same_level
    assert "решает оператор" in finding.priority_verdict()


# ---------------------------------------------------------------------------
# Determinism & serialisation
# ---------------------------------------------------------------------------

def test_outcome_is_deterministic_for_the_same_input():
    def build():
        return evaluate([
            _contract("порядок элементов", "стабильный порядок"),
            _advisor("порядок элементов", "сортировать по имени"),
            _advisor("сетевые вызовы", "тянуть через API"),
            _contract("сетевые вызовы", "сеть запрещена"),
        ])

    assert build().to_dict() == build().to_dict()


def test_blank_subject_is_ignored():
    outcome = evaluate([
        _contract("   ", "стабильный порядок"),
        _advisor("", "сортировать по имени"),
    ])
    assert outcome.mode == "proceed"


def test_to_dict_is_json_shaped():
    outcome = reviewer_vs_contract(
        subject="порядок элементов",
        contract_demand="стабильный порядок",
        reviewer_demand="сортировать по имени",
    )
    payload = outcome.to_dict()
    assert payload["mode"] == "blocked"
    assert isinstance(payload["findings"], list)
    assert payload["allowed_action"] == "report_conflict"
    assert "git_commit" in payload["forbidden_actions"]

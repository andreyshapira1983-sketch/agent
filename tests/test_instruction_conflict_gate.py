"""Tests for core.instruction_conflict_gate.

The four scenarios in the operator's exam are pinned here as named tests, so a
regression in the turnstile shows up as a failing scenario rather than as a
quietly-applied reviewer suggestion.
"""
from __future__ import annotations

import pytest

from core.instruction_conflict_gate import (
    AUTHORITY_RANK,
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


def test_single_directive_proceeds():
    outcome = evaluate([_contract("порядок вывода", "стабильный порядок")])
    assert outcome.mode == "proceed"


def test_different_subjects_are_not_a_conflict():
    """Two requirements about different things must both be satisfied."""
    outcome = evaluate([
        _contract("порядок вывода", "стабильный порядок"),
        _advisor("имена переменных", "использовать snake_case"),
    ])
    assert outcome.mode == "proceed"


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
# The four exam scenarios
# ---------------------------------------------------------------------------

def test_scenario_stable_order_vs_sort_by_name():
    """Спецификация требует стабильный порядок, ревьюер просит сортировать по имени."""
    outcome = reviewer_vs_contract(
        subject="порядок элементов в выводе",
        contract_demand="сохранять стабильный порядок",
        reviewer_demand="сортировать по имени",
    )
    assert outcome.is_blocked
    finding = outcome.findings[0]
    assert finding.higher.source_level == "task_contract"
    assert finding.lower.source_level == "advisor"


def test_scenario_no_network_vs_fetch_via_api():
    """Контракт запрещает сетевые вызовы, ревьюер предлагает получить данные через API."""
    outcome = evaluate([
        _contract("получение данных", "сетевые вызовы запрещены"),
        _advisor("получение данных", "получать данные через API"),
    ])
    assert outcome.is_blocked
    assert outcome.findings[0].higher.source_level == "task_contract"


def test_scenario_test_requires_exception_reviewer_wants_it_swallowed():
    """Тест требует сохранения исключения, ревьюер просит его проглотить."""
    outcome = evaluate([
        Directive(
            source_level="test_expectation",
            source_name="tests/test_loader.py::test_raises",
            subject="обработка исключения загрузчика",
            demand="исключение пробрасывается наружу",
        ),
        _advisor(
            "обработка исключения загрузчика",
            "поймать исключение и вернуть None",
        ),
    ])
    assert outcome.is_blocked
    finding = outcome.findings[0]
    assert finding.higher.source_level == "test_expectation"
    assert not finding.same_level


def test_scenario_task_forbids_edits_but_repo_requires_registration():
    """Задача запрещает менять файлы, инвариант требует регистрации модуля.

    Here the task contract outranks the repo invariant — and the gate must
    still block. Obeying the higher source silently would leave an unregistered
    module behind, which is exactly the decision that belongs to the operator.
    """
    outcome = evaluate([
        _contract(
            "регистрация нового модуля",
            "существующие файлы не изменять",
        ),
        Directive(
            source_level="repo_invariant",
            source_name="docs/AGENT_DOCTRINE.md",
            subject="регистрация нового модуля",
            demand="каждый новый модуль зарегистрирован в индексе",
        ),
    ])
    assert outcome.is_blocked, (
        "a clear authority winner must NOT let the agent resolve the conflict "
        "on its own — the ranking names the priority, it does not grant a "
        "licence to proceed"
    )
    assert outcome.findings[0].higher.source_level == "task_contract"


# ---------------------------------------------------------------------------
# Turnstile behaviour
# ---------------------------------------------------------------------------

def test_blocked_forbids_every_state_mutating_action():
    outcome = reviewer_vs_contract(
        subject="порядок элементов",
        contract_demand="стабильный порядок",
        reviewer_demand="сортировать по имени",
    )
    for action in ("write_file", "modify_test", "git_add", "git_commit",
                   "git_push", "apply_patch", "delete_file"):
        assert outcome.is_forbidden(action), action


def test_blocked_allows_exactly_one_action():
    outcome = reviewer_vs_contract(
        subject="порядок элементов",
        contract_demand="стабильный порядок",
        reviewer_demand="сортировать по имени",
    )
    assert outcome.allowed_action == "report_conflict"


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

def test_advisor_is_the_weakest_source():
    assert AUTHORITY_RANK["advisor"] == max(AUTHORITY_RANK.values())


def test_operator_is_the_strongest_source():
    assert AUTHORITY_RANK["operator"] == min(AUTHORITY_RANK.values())


def test_ranking_order_is_the_documented_one():
    order = [
        level for level, _ in sorted(
            AUTHORITY_RANK.items(), key=lambda item: item[1]
        )
    ]
    assert order == [
        "operator",
        "task_contract",
        "test_expectation",
        "repo_invariant",
        "local_convention",
        "advisor",
    ]


def test_unknown_level_ranks_below_every_known_one():
    """An unrecognised label must never win an argument by accident."""
    unknown = Directive(
        source_level="whatever",  # type: ignore[arg-type]
        source_name="неизвестный источник",
        subject="порядок элементов",
        demand="сортировать по имени",
    )
    assert unknown.rank > max(AUTHORITY_RANK.values())
    outcome = evaluate([
        unknown,
        _advisor("порядок элементов", "стабильный порядок"),
    ])
    assert outcome.is_blocked
    assert outcome.findings[0].higher.source_level == "advisor"


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
# The six-point report
# ---------------------------------------------------------------------------

def test_report_has_all_six_required_points():
    outcome = evaluate([
        _contract(
            "порядок элементов",
            "стабильный порядок",
            quote="Порядок вывода должен быть стабильным между запусками.",
            locator="docs/spec.md:12",
        ),
        _advisor(
            "порядок элементов",
            "сортировать по имени",
            quote="Отсортируй по имени, так читается удобнее.",
            source="код-ревью PR #214",
        ),
    ])
    report = outcome.report()
    for point in ("1.", "2.", "3.", "4.", "5.", "6."):
        assert point in report, f"report is missing point {point}"


def test_report_quotes_both_sources_verbatim():
    outcome = evaluate([
        _contract(
            "порядок элементов",
            "стабильный порядок",
            quote="Порядок вывода должен быть стабильным между запусками.",
            locator="docs/spec.md:12",
        ),
        _advisor(
            "порядок элементов",
            "сортировать по имени",
            quote="Отсортируй по имени, так читается удобнее.",
            source="код-ревью PR #214",
        ),
    ])
    report = outcome.report()
    assert "Порядок вывода должен быть стабильным между запусками." in report
    assert "Отсортируй по имени, так читается удобнее." in report
    assert "docs/spec.md:12" in report
    assert "код-ревью PR #214" in report


def test_report_names_the_priority_and_the_stop():
    outcome = reviewer_vs_contract(
        subject="порядок элементов",
        contract_demand="стабильный порядок",
        reviewer_demand="сортировать по имени",
    )
    report = outcome.report()
    assert "выше по полномочиям" in report
    assert "Код и тесты не изменялись" in report
    assert "решения оператора" in report


def test_report_lists_the_resolution_procedure():
    outcome = reviewer_vs_contract(
        subject="порядок элементов",
        contract_demand="стабильный порядок",
        reviewer_demand="сортировать по имени",
    )
    report = outcome.report()
    assert "Процедура разрешения" in report
    for step in outcome.resolution_steps:
        assert step in report


def test_cite_falls_back_to_demand_without_a_quote():
    directive = _contract("порядок элементов", "стабильный порядок")
    assert "стабильный порядок" in directive.cite()


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


def test_multiple_subjects_produce_one_finding_each():
    outcome = evaluate([
        _contract("порядок элементов", "стабильный порядок"),
        _advisor("порядок элементов", "сортировать по имени"),
        _contract("сетевые вызовы", "сеть запрещена"),
        _advisor("сетевые вызовы", "тянуть через API"),
    ])
    assert len(outcome.findings) == 2
    assert {finding.subject for finding in outcome.findings} == {
        "порядок элементов", "сетевые вызовы",
    }


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


@pytest.mark.parametrize("action", FORBIDDEN_WHILE_CONFLICTED)
def test_every_forbidden_action_is_reported_in_point_four(action):
    outcome = reviewer_vs_contract(
        subject="порядок элементов",
        contract_demand="стабильный порядок",
        reviewer_demand="сортировать по имени",
    )
    assert action in outcome.report()

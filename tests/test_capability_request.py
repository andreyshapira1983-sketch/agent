from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from core.capability_request import (
    CapabilityRequest,
    detect_capability_type,
    propose_capability_request,
)


@pytest.mark.parametrize(
    ("goal", "capability_type"),
    [
        ("Хочу, чтобы ты сам сообщал мне, когда нужно решение", "telegram"),
        ("Следи за важными письмами и готовь черновики", "email"),
        ("Следи за Upwork и ищи подходящие задачи", "upwork"),
        ("Создай отдельный подагент для проверки источников", "subagent"),
        ("Запусти долгую сессию на 7 часов", "long_work_session"),
        ("Для этой задачи нужна модель выше", "model_tier_upgrade"),
        ("Записывай проверенные знания в память", "persistent_memory_write"),
        ("Дай доступ к файлам проекта", "file_workspace_access"),
        ("Мониторь сайт и RSS обновления", "web_monitor"),
    ],
)
def test_detect_capability_type(goal: str, capability_type: str) -> None:
    assert detect_capability_type(goal) == capability_type


def test_detect_capability_type_unknown_for_non_string() -> None:
    assert detect_capability_type(None) == "unknown"  # type: ignore[arg-type]


def test_telegram_request_is_bounded_and_approval_required() -> None:
    request = propose_capability_request("Хочу, чтобы ты сам сообщал мне, когда нужно решение")

    assert request.capability_type == "telegram"
    assert request.approval_required is True
    assert "owner notifications" in request.write_scope
    assert "message third parties" in request.forbidden_actions
    assert request.budget_limits["max_messages_per_day"] == 10


def test_email_request_forbids_destructive_and_outbound_actions() -> None:
    request = propose_capability_request("Следи за важными письмами")

    assert request.capability_type == "email"
    assert request.risk_level == "high"
    assert "send email without approval" in request.forbidden_actions
    assert "delete email" in request.forbidden_actions
    assert "draft replies only" in request.write_scope


def test_upwork_request_blocks_client_contact_and_spend() -> None:
    request = propose_capability_request("Следи за Upwork и ищи подходящие задачи")

    assert request.capability_type == "upwork"
    assert "send proposals" in request.forbidden_actions
    assert "contact clients" in request.forbidden_actions
    assert "spend money" in request.forbidden_actions
    assert request.budget_limits["max_jobs_per_day"] == 10


def test_unknown_request_asks_for_narrower_boundary() -> None:
    request = propose_capability_request("Сделай что-нибудь полезное сам")

    assert request.capability_type == "unknown"
    assert request.risk_level == "low"
    assert "No access requested yet" in request.requested_access
    assert "activate connectors" in request.forbidden_actions


def test_request_to_dict_is_json_serializable() -> None:
    request = propose_capability_request("Нужно подключить Telegram")
    raw = json.dumps(request.to_dict())

    assert json.loads(raw)["capability_type"] == "telegram"
    assert request.request_id.startswith("capreq_")
    assert request.created_at


def test_user_summary_contains_required_operator_fields() -> None:
    text = propose_capability_request("Следи за почтой").user_summary()

    assert "capability request" in text
    assert "forbidden_actions" in text
    assert "budget_limits" in text
    assert "approval_required: true" in text


def test_request_is_frozen() -> None:
    request = propose_capability_request("Следи за Upwork")
    with pytest.raises((FrozenInstanceError, AttributeError)):
        request.goal = "changed"  # type: ignore[misc]


def test_empty_goal_rejected() -> None:
    with pytest.raises(ValueError, match="goal must be non-empty"):
        propose_capability_request("   ")


def test_budget_limits_must_be_non_negative() -> None:
    with pytest.raises(ValueError, match="budget limit values"):
        CapabilityRequest(
            goal="x",
            capability_type="unknown",
            why_needed="x",
            requested_access="x",
            read_scope=("goal",),
            write_scope=(),
            forbidden_actions=(),
            budget_limits={"bad": -1},
            human_risk_summary="x",
            fallback_if_denied="x",
        )


def test_approval_required_cannot_be_disabled() -> None:
    with pytest.raises(ValueError, match="require approval"):
        CapabilityRequest(
            goal="x",
            capability_type="unknown",
            why_needed="x",
            requested_access="x",
            read_scope=("goal",),
            write_scope=(),
            forbidden_actions=(),
            budget_limits={"max_model_calls": 0},
            human_risk_summary="x",
            fallback_if_denied="x",
            approval_required=False,
        )

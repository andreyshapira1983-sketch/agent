"""Three fail-safe branches in the chat bridge, none of which had a witness.

All three were found the same way on 2026-08-20: a mutation flipped the branch
and the entire suite stayed green. Each is a place where the bridge decides
what it may claim on the operator's behalf, and in each the safe answer is the
modest one.

1. A message for which no reply was produced must not be reported as handled.
   Both call sites — `cli/repl.py` and `cli/one_shot.py` — treat a True return
   as "the turn is consumed", so an inverted branch swallows the operator's
   message and answers nothing.

2. When no model can be consulted, the deterministic route survives.
   `_model_says_conversation` exists so the model may only VETO an obvious
   conversational false positive. Inverted, a missing or broken planner model
   turns into a veto of every gated intent — the failure mode the docstring
   promises cannot happen, and the one that makes an operator's command
   silently do nothing.

3. A stop order cancels only work whose goal overlaps it. `_tokens_overlap`
   matches on a shared six-character prefix so Russian inflection does not
   split a subject. Inverted, any six-character DIFFERENCE counts as overlap,
   and a stop aimed at one task destroys the rest of the queue.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cli import intent_bridge


def test_a_message_with_no_reply_is_not_reported_as_handled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(intent_bridge, "_local_operator_reply", lambda text, agent: None)
    assert intent_bridge._handle_local_operator_reply("что там по задачам", object()) is False


def test_a_message_with_a_reply_is_reported_as_handled(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Boundary pin: refusing to claim anything would satisfy the test above
    while making the local reply path dead."""
    monkeypatch.setattr(intent_bridge, "_local_operator_reply", lambda text, agent: "готово")
    monkeypatch.setattr(intent_bridge, "_record_routed_turn", lambda *a, **k: None)
    assert intent_bridge._handle_local_operator_reply("привет", object()) is True
    assert "готово" in capsys.readouterr().out


def test_no_model_means_the_deterministic_route_survives() -> None:
    class _NoModel:
        def for_role(self, role):
            raise RuntimeError("no planner model configured")

    agent = SimpleNamespace(model_router=_NoModel())
    intent = SimpleNamespace(kind="project_health")

    assert intent_bridge._model_says_conversation("как дела", intent, agent) is False, (
        "with no model to consult the bridge vetoed the deterministic route — "
        "the operator's command would silently do nothing"
    )


def test_a_stop_order_does_not_reach_across_unrelated_goals() -> None:
    stop = intent_bridge._goal_tokens("останови рефакторинг парсера конфигурации")
    other = intent_bridge._goal_tokens("собери отчёт по использованию бюджета")
    assert intent_bridge._tokens_overlap(stop, other) is False, (
        "a stop order overlapped a goal it does not name — cancelling it would "
        "destroy queued work the operator never mentioned"
    )


def test_inflection_of_the_same_subject_still_counts_as_overlap() -> None:
    """The other boundary: the prefix rule exists so «программированию» finds
    «программировать». A matcher that never overlaps would satisfy the test
    above and make every stop order a no-op."""
    a = intent_bridge._goal_tokens("научись программированию на python")
    b = intent_bridge._goal_tokens("продолжай программировать модуль")
    assert intent_bridge._tokens_overlap(a, b) is True

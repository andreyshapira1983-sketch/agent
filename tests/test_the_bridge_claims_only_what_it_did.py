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

from cli import intent_bridge


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

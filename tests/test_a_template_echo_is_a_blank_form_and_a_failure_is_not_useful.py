"""Две живые идеи из устаревших запросов #352 и #349, перенесённые 25.09 поверх main.

1. Бланк, заполненный словами образца, — пустой бланк: модель вернула пример
   спецификации дословно, проверка «непусто ли» его приняла, 48 прогонов ушли
   впустую (живой реестр 2026-09-18).
2. Провал не «полезный» цикл: сводка журнала кампании считала полезным всё,
   кроме простоя, повтора и исключения — 144 из 160 при восьми настоящих.
"""
from __future__ import annotations

from core.campaign_ledger import summarise_ledger
from core.causal_climb_action import _SPEC_PLACEHOLDERS, parse_experiment


def test_the_template_echo_is_refused_and_a_real_spec_passes() -> None:
    a, b, effect = _SPEC_PLACEHOLDERS
    assert parse_experiment(f"[exp: reasoning_action_check | A={a} | B={b} | след={effect}]") is None
    assert parse_experiment(f"[exp: reasoning_action_check | A=два шага | B={b} | след=разный ответ]") is None
    real = parse_experiment("[exp: reasoning_action_check | A=план без проверки | B=план с проверкой | "
                            "след=mismatch в отчёте]")
    assert real is not None and real.effect == "mismatch в отчёте"


def test_the_birth_prompt_example_is_built_from_the_same_words() -> None:
    import inspect

    import core.causal_climb_action as mod

    src = inspect.getsource(mod._birth_experiment_spec)
    assert "format(*_SPEC_PLACEHOLDERS)" in src, "the example and the refusal are one source"
    assert not any(word in src for word in _SPEC_PLACEHOLDERS), "no second copy of the placeholder words"


def test_a_failed_cycle_is_not_counted_useful() -> None:
    rows = [
        {"cycle": 1, "result": "failed", "action": "run_claim_experiment", "work_done": False},
        {"cycle": 2, "result": "failed", "action": "run_claim_experiment", "work_done": False},
        {"cycle": 3, "result": "completed", "action": "propose", "work_done": True, "proposal": "p1"},
        {"cycle": 4, "result": "completed", "action": "old", "artifact": "data/notes/x.md"},  # до поля work_done
        {"cycle": 5, "idle": True, "result": "idle"},
    ]
    head = summarise_ledger(rows).splitlines()[1]
    assert "useful=2" in head and "failed=2" in head, head

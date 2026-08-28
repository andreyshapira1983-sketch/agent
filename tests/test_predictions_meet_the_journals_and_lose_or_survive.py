"""Слайс 2 органа подъёма: предсказания встречают журналы — и гибнут или живут.

Проект: docs/audit/CAUSAL_CLIMB_ORGAN_DESIGN.md (MIR-096). Конституция слайса:
- различение ДЕТЕРМИНИРОВАННОЕ: греп журналов, ноль вызовов модели — слово
  автора не улика (Huang ICLR'24), улика — файл;
- проба — машинная часть предсказания: `[probe: путь | подстрока | есть|нет]`,
  пути только внутри logs/ и data/, побег из рабочей области — проба невалидна
  и НЕ читается;
- опровергнутая гипотеза получает `refuted_by` с наблюдённым фактом;
- выбор — только когда живым остался РОВНО один (среди всех, не среди
  проверенных): непроверяемое пробой ждёт вмешательства, слайс 3;
- все опровергнуты — заявка REFUTED целиком: опровержение — тоже знание;
- неведение — не вердикт: нечитаемая/несуществующая цель пробы не опровергает.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from core.causal_claim_store import load_claims, save_claim
from core.causal_climb import propose_explanation
from core.causal_climb_action import (
    discriminable_claims,
    discriminate_causal_claim,
    parse_probe,
)
from core.causal_lesson import CausalClaim, Observation, state_of


def _claim(predicts_pair: tuple[str, str]) -> CausalClaim:
    return CausalClaim(
        observation=Observation(
            episode_id="ep-1", trace_id="", run_id="r",
            defect_signals=("reasoning_action_mismatch",),
            evidence_refs=("log:t",), observed_mismatch="m",
        ),
        explanations=(
            propose_explanation("гипотеза А", author="agent",
                                predicts=predicts_pair[0]),
            propose_explanation("гипотеза Б", author="agent",
                                predicts=predicts_pair[1]),
        ),
    )


def _agent():
    events: list[tuple[str, dict]] = []
    agent = SimpleNamespace(
        log=SimpleNamespace(log=lambda e, p: events.append((e, p))),
    )
    agent.events = events
    return agent


def _seed_journal(tmp_path: Path, rel: str, text: str) -> None:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_the_probe_grammar_parses_and_rejects(tmp_path: Path) -> None:
    """Красный свидетель разбора: валидное читается, побег — нет."""
    ok = parse_probe("в журнале нет поля tools [probe: logs/planner.jsonl | \"tools\" | нет]")
    assert ok is not None and ok.needle == '"tools"' and ok.expect_present is False

    assert parse_probe("без пробы вовсе") is None
    assert parse_probe("[probe: ../secrets.txt | x | есть]") is None, "побег из области"
    assert parse_probe("[probe: C:/win/x | x | есть]") is None, "абсолютный путь"
    assert parse_probe("[probe: core/loop.py | x | есть]") is None, "вне logs/ и data/"


def test_a_wrong_prediction_is_refuted_by_the_journal(tmp_path: Path) -> None:
    """Проба «нет», журнал говорит «есть» — гипотеза получает опровержение."""
    _seed_journal(tmp_path, "logs/planner.jsonl", '{"tools": ["x"]}\n')
    key = save_claim(_claim((
        'поля tools нет [probe: logs/planner.jsonl | "tools" | нет]',
        'словарь беден [probe: logs/planner.jsonl | "нет_такой_строки" | есть]',
    )), workspace=tmp_path)

    outcome = discriminate_causal_claim(agent=_agent(), workspace=tmp_path)

    assert outcome.did_work
    claim, extra = load_claims(tmp_path)[0]
    assert extra["key"] == key
    dead = [e for e in claim.explanations if not e.alive]
    assert len(dead) == 2, "обе пробы провалились — обе гипотезы мертвы"
    assert claim.refuted_reason.strip(), "все объяснения пали — заявка REFUTED"
    assert state_of(claim) == "REFUTED"


def test_a_sole_survivor_is_chosen_by_the_outcome_not_the_author(tmp_path: Path) -> None:
    _seed_journal(tmp_path, "logs/planner.jsonl", '{"note": "no tools field"}\n')
    save_claim(_claim((
        'поля tools нет [probe: logs/planner.jsonl | "tools" | нет]',
        'есть маркер X [probe: logs/planner.jsonl | "маркер X" | есть]',
    )), workspace=tmp_path)

    outcome = discriminate_causal_claim(agent=_agent(), workspace=tmp_path)

    assert outcome.did_work
    claim, _extra = load_claims(tmp_path)[0]
    alive = [e for e in claim.explanations if e.alive]
    assert len(alive) == 1
    assert claim.chosen == alive[0].statement, "выбор = исход различения"


def test_an_unprobed_rival_blocks_the_choice(tmp_path: Path) -> None:
    """Живой без пробы — соперник не разобран: выбора нет, ждём вмешательства."""
    _seed_journal(tmp_path, "logs/planner.jsonl", "x\n")
    save_claim(_claim((
        'есть маркер [probe: logs/planner.jsonl | "маркер" | есть]',  # опровергнется
        "нужно вмешательство, журналом не проверить",                   # без пробы
    )), workspace=tmp_path)

    discriminate_causal_claim(agent=_agent(), workspace=tmp_path)

    claim, _extra = load_claims(tmp_path)[0]
    assert claim.chosen == "", "непроверенный соперник жив — автор не дожимает"
    assert not claim.refuted_reason.strip()


def test_an_unreadable_target_refutes_nothing(tmp_path: Path) -> None:
    """Незнание — не приговор: нет файла — нет вердикта, гипотеза жива."""
    save_claim(_claim((
        'есть маркер [probe: logs/missing.jsonl | "маркер" | есть]',
        "без пробы",
    )), workspace=tmp_path)
    agent = _agent()

    outcome = discriminate_causal_claim(agent=agent, workspace=tmp_path)

    claim, _extra = load_claims(tmp_path)[0]
    assert all(e.alive for e in claim.explanations)
    assert not outcome.did_work, "ни одного вердикта — работа не сделана"
    inconclusive = [p for e, p in agent.events if e == "causal_probe_inconclusive"]
    assert inconclusive, "неведение записано поимённо, не проглочено"


def test_the_signal_lists_only_undecided_probed_claims(tmp_path: Path) -> None:
    save_claim(_claim((
        'а [probe: logs/a.jsonl | "x" | есть]',
        "б без пробы",
    )), workspace=tmp_path)

    listed = discriminable_claims(tmp_path)

    assert len(listed) == 1

    # решённая заявка уходит из сигнала
    import dataclasses
    claim, _extra = load_claims(tmp_path)[0]
    save_claim(dataclasses.replace(claim, chosen="а"), workspace=tmp_path)
    assert discriminable_claims(tmp_path) == ()


def test_the_action_is_born_with_its_executor() -> None:
    """Анти-MIR-179 + рождение кандидата от сигнала."""
    import inspect

    from core import campaign_io as mod
    from core.best_next_action import select_best_next_action

    picked = select_best_next_action(discriminable_claims_count=2)
    silent = select_best_next_action(discriminable_claims_count=0)
    assert picked.action == "discriminate_causal_claim"
    assert silent.action != "discriminate_causal_claim"
    assert '"discriminate_causal_claim"' in inspect.getsource(mod._default_execute_action)


def test_discrimination_never_calls_a_model(tmp_path: Path) -> None:
    """Слово автора — не улика: у различения нет модели вовсе."""
    import inspect

    from core import causal_climb_action as mod

    src = inspect.getsource(mod.discriminate_causal_claim)
    assert ".complete(" not in src and "llm" not in src

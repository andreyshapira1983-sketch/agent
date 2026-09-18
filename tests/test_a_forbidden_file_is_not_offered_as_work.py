"""Запретный файл не предлагается как работа (живой прогон 18.09 01:59).

Владелец запустил агента и прервал через 53 секунды: двадцать один цикл
подряд, `campaign_engineering_proposed` со `status="no_grounded_target"`, ноль
предложений. Со стороны это выглядело зависанием.

Правда была такая. Цель прогона:

    Create a reviewed proposal to split the oversized module
    core/self_build_producer.py into smaller, more manageable components.

`core/self_build_producer.py` — это САМ механизм самосборки, он стоит в
`CRITICAL_DENY` рядом с `main.py` и `loop.py`. Агенту запрещено его трогать,
и это верно. Бэклог того часа: семь кандидатов, из них **пять доступных**
(`model_router`, `smart_memory`, `operator_intent_patterns`,
`step_sanitizer`, `best_next_action`) и два запретных
(`self_build_producer`, `autonomous_runtime`). Агент выбрал запретный и бился
в него двадцать один раз, имея пять рабочих рядом.

Два места, где это стало возможным, — и оба лечатся здесь.

**Первое: правда рождается и умирает по дороге.** Управляющий отвечает честно,
замер пробником:

    decision: no_target
    detail  : grounded target 'core/self_build_producer.py' is critical

Эта строка доезжает до `ProducerReport.reason`. А
`campaign_io._propose_engineering_step` пишет в журнал `status`, `approval_id`
и `target` — и ВЫБРАСЫВАЕТ `reason`. Наверх уходит голое
`no_grounded_target`, которое читается как «работы не осталось», хотя правда
была «этот файл вне твоих полномочий, возьми соседний». Докстринг того же шага
гласит: «A refusal is surfaced by name in the log, never hidden». Имени в
журнале не было.

**Второе: запрет не доезжает до выбора цели.** `charter_goal._backlog_lines`
отдаёт хартии верхние шесть кандидатов как «real, measured candidates», а
запрос рядом просит «name ONE real engineering candidate from your backlog and
repair it». Запретные файлы в этом списке неотличимы от рабочих, и хартия
берёт их наравне.

Мерка здесь простая: **работой вправе называться только то, что агенту
позволено сделать; отказ обязан назвать свою причину.**
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import core.budget_kill_switch as kill_mod
import core.campaign_io as mod
import core.charter_goal as charter
import core.safe_vcs as vcs_mod
import core.self_build_memory as memory_mod
import core.self_build_producer as sbp
from core.backlog_selector import BacklogCandidate


def _candidate(target: str) -> BacklogCandidate:
    """Кандидат в той же форме, в какой его отдал живой бэклог."""
    return BacklogCandidate(
        target_path=f"split:{target}",
        signal_source="oversized_module",
        evidence_ref=f"{target}:1",
        problem_quote=f"{target} has too many code lines; split into modules",
        proposed_change="Scope a minimal PR addressing this backlog item.",
        proof_of_value="A new or updated test proving the change.",
        expected_effect=f"Backlog item split:{target} progressed.",
        confidence=0.4,
        score=0.9,
    )


#: Ровно те два, что живой бэклог отдал запретными, и три из пяти рабочих.
_FENCED = ("core/self_build_producer.py", "core/autonomous_runtime.py")
_ALLOWED = ("core/model_router.py", "core/smart_memory.py",
            "core/step_sanitizer.py")


@pytest.fixture
def _journal(monkeypatch, tmp_path):
    """Шаг предложения без машинерии вокруг: важен только журнал."""
    events: list[tuple[str, dict]] = []

    report = SimpleNamespace(
        status="no_grounded_target", approval_id="", target_path="",
        reason="grounded target 'core/self_build_producer.py' is critical",
    )
    monkeypatch.setattr(sbp, "produce_self_apply_proposal", lambda **kw: report)
    monkeypatch.setattr(vcs_mod, "SafeVCS", lambda **kw: None)
    monkeypatch.setattr(memory_mod, "recently_vetoed_self_build_targets",
                        lambda agent: ())
    monkeypatch.setattr(kill_mod, "default_path", lambda root: tmp_path / "k")
    monkeypatch.setattr(
        kill_mod, "BudgetKillSwitch",
        lambda **kw: SimpleNamespace(status=lambda _x: None),
    )
    monkeypatch.setattr(mod, "_log",
                        lambda agent, event, payload: events.append((event, payload)))

    agent = SimpleNamespace(
        log=None,
        model_router=SimpleNamespace(for_role=lambda role: None),
    )
    mod._propose_engineering_step(
        agent=agent, workspace=str(tmp_path), approval_inbox=None,
        target="core/self_build_producer.py",
    )
    return events, report


# ── свидетели ─────────────────────────────────────────────────────────────


def test_a_refusal_names_its_reason_in_the_journal(_journal) -> None:
    """Двадцать один отказ подряд не сказал, ПОЧЕМУ отказано.

    Управляющий назвал причину («is critical»), шаг её выбросил. Голый
    `no_grounded_target` читается как «работы нет» — и петля выглядит
    исправной работой.
    """
    events, report = _journal
    proposed = [p for e, p in events if e == "campaign_engineering_proposed"]

    assert proposed, f"шаг не записал исход вовсе: {events}"
    assert proposed[0].get("reason") == report.reason, (
        "отказ не назвал причину: в журнале "
        f"{proposed[0]!r}, а управляющий сказал {report.reason!r}"
    )


def test_a_forbidden_file_is_not_offered_to_the_charter(monkeypatch) -> None:
    """Хартии показывали файл, который ей запрещено чинить.

    Живой бэклог отдавал `core/self_build_producer.py` вторым в списке
    «real, measured candidates», рядом с просьбой назвать один и починить.
    """
    monkeypatch.setattr(
        "core.backlog_selector.load_backlog",
        lambda root, **kw: [_candidate(t) for t in _FENCED + _ALLOWED],
    )

    lines = charter._backlog_lines(Path("."))

    offered = " | ".join(lines)
    for fenced in _FENCED:
        assert fenced not in offered, (
            f"хартии предложен запретный {fenced}: {offered}"
        )


def test_the_fence_is_applied_before_the_list_is_cut_to_six(
    monkeypatch,
) -> None:
    """Ревизия PR #347: срез в шесть обязан браться ПОСЛЕ отсева.

    Свидетель выше держал пять кандидатов и потому не отличал порядок
    действий: `[:6]` от пяти — те же пять. Здесь запретных ровно столько,
    сколько мест в списке, и они стоят первыми. Прежний ход (сперва срез,
    потом ничего) отдал бы хартии шесть запретных строк и ни одной рабочей.
    """
    fenced = tuple(f"core/self_build_producer.py#{i}" for i in range(6))
    allowed = tuple(f"core/model_router.py#{i}" for i in range(7))
    monkeypatch.setattr(
        "core.backlog_selector.load_backlog",
        lambda root, **kw: [_candidate(t) for t in fenced + allowed],
    )
    monkeypatch.setattr(
        sbp, "_is_self_build_target_allowed",
        lambda t: "self_build_producer" not in t,
    )

    lines = charter._backlog_lines(Path("."))

    assert len(lines) == 6, f"срез в шесть не соблюдён: {len(lines)}"
    offered = " | ".join(lines)
    assert "self_build_producer" not in offered, (
        f"запретные съели места рабочих: {offered}"
    )
    for i in range(6):
        assert f"core/model_router.py#{i} " in offered, (
            f"рабочий кандидат #{i} не доехал: {offered}"
        )


def test_the_live_backlog_offers_the_charter_nothing_fenced(
    monkeypatch,
) -> None:
    """Свидетель на НАСТОЯЩЕМ бэклоге этого репозитория, без подделок.

    Ревизия PR #347: соседний контроль сверял забор лишь с руками
    выписанным списком и остался бы зелёным, заведись в бэклоге другой
    запретный файл. Здесь бэклог читается живьём, и мерка та же самая:
    ни одна предложенная строка не вправе указывать на заповеданный файл.
    """
    from core.backlog_selector import load_backlog

    root = Path(__file__).resolve().parent.parent
    fenced = [
        c for c in load_backlog(root)
        if not any(sbp._is_self_build_target_allowed(t)
                   for t in sbp._candidate_concrete_targets(c, root))
    ]
    if not fenced:
        pytest.skip("в живом бэклоге сейчас нет запретных кандидатов")

    offered = " | ".join(charter._backlog_lines(root))

    for candidate in fenced:
        evidence = str(getattr(candidate, "evidence_ref", "")).split(":")[0]
        assert evidence and evidence not in offered, (
            f"живому бэклогу удалось предложить запретный {evidence}: "
            f"{offered}"
        )


# ── контроли ──────────────────────────────────────────────────────────────


def test_the_allowed_candidates_are_still_offered(monkeypatch) -> None:
    """Контроль: забор не должен выкосить рабочие цели заодно.

    Пять доступных файлов того же часа обязаны дойти до хартии.
    """
    monkeypatch.setattr(
        "core.backlog_selector.load_backlog",
        lambda root, **kw: [_candidate(t) for t in _FENCED + _ALLOWED],
    )

    offered = " | ".join(charter._backlog_lines(Path(".")))

    for good in _ALLOWED:
        assert good in offered, f"рабочий кандидат {good} потерян: {offered}"


def test_the_fence_refuses_the_self_build_machinery_by_policy() -> None:
    """Контроль ПОЛИТИКИ забора, не бэклога (ревизия PR #347).

    Прежнее имя обещало сверку с живым бэклогом, а тело сверяло лишь руками
    выписанные списки — ровно та подмена имени содержанием, против которой
    написан этот файл. Живой бэклог проверяет сосед выше; здесь измеряется
    одно: как забор судит те семь путей, что вскрыл прогон 18.09.
    """
    for fenced in _FENCED:
        assert not sbp._is_self_build_target_allowed(fenced), fenced
    for good in _ALLOWED:
        assert sbp._is_self_build_target_allowed(good), good

"""Критерий успеха доезжает до исполнения — и судит его.

WHY THIS EXISTS. Аудит автономности 2026-09-17, находки 4 и 5. Две половины
одного дефекта:

* `propose_charter_goal` возвращает `CharterGoalReport` с обязательным
  `success_check` («цель без проверки — желание»), а `_pick_next_goal` отдавал
  наружу ОДНУ строку — `pick.goal`. Критерий умирал в месте выбора и до
  исполнителя не доезжал вовсе;
* `_task_goal` объявлял задачу `done` по ОДНОМУ признаку: ответ модели не
  пуст. Никакого независимого наблюдения мира. «Я создал файл» и созданный
  файл были для системы одним и тем же событием.

Вместе это давало цикл, который нельзя замкнуть: цель ставится с проверкой,
исполняется без неё, а результатом считается рассказ исполнителя о себе.

Чего здесь НЕ утверждается: что система понимает любой критерий. Критерий —
свободный текст. Проверяется ровно то, что можно наблюдать не спрашивая
модель: НАЗВАННЫЙ в критерии файл. Когда критерий не называет наблюдаемого
следа, это записывается словом `unverifiable` — «не проверяли» не равно
«сошлось».
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from core.autonomous_runtime import AutonomousRuntime
from core.autonomous_runtime_types import AutonomousRuntimeConfig, AutonomousTask
from core.campaign_types import CampaignConfig
from core.success_check import named_artifacts, observe_success_check

_BURN_IN_CHECK = "файл result.json существует и содержит status=ready"


# --------------------------------------------------------------------------- #
# 1. Чтение критерия: что вообще можно наблюдать                               #
# --------------------------------------------------------------------------- #

def test_a_named_file_is_the_observable_part_of_a_criterion() -> None:
    assert named_artifacts(_BURN_IN_CHECK) == ("result.json",)
    assert named_artifacts("создан docs/notes/plan.md и он не пуст") == (
        "docs/notes/plan.md",
    )


def test_a_criterion_without_a_named_file_is_not_pretended_to_be_checked() -> None:
    """«Проверка не названа» — отдельное состояние, а не молчаливое «да»."""
    assert named_artifacts("агент стал полезнее") == ()
    assert named_artifacts("") == ()

    verdict = observe_success_check("агент стал полезнее", Path("."))
    assert verdict["verdict"] == "unverifiable"


def test_the_criterion_is_read_from_the_world_not_from_the_answer(tmp_path: Path) -> None:
    missing = observe_success_check(_BURN_IN_CHECK, tmp_path)
    assert missing["verdict"] == "missing"
    assert missing["missing"] == ["result.json"]

    (tmp_path / "result.json").write_text('{"status": "ready"}', encoding="utf-8")
    found = observe_success_check(_BURN_IN_CHECK, tmp_path)
    assert found["verdict"] == "verified"
    assert found["missing"] == []


# ── Ревизия PR #333: наличие файла — ещё не критерий ──────────────────────────
#
# Первый заход довёл проверку до вопроса «существует ли названный файл», и это
# было больше, чем «модель сказала, что сделала». Но критерий говорит не только
# про существование: `_BURN_IN_CHECK` требует `status=ready`, а проверялся
# только размер. Файл со словом `broken` объявлялся `verified`. Второе: путь
# брался как `root / relpath`, то есть `tmp/../../outside/result.json` уводил
# проверяющего ЗА пределы рабочей копии — и след, положенный снаружи, засчитывал
# цель внутри.


def test_a_criterion_is_not_met_by_the_wrong_content(tmp_path: Path) -> None:
    """Названная пара «ключ=значение» обязана найтись в названном файле.

    Иначе вердикт `verified` означает «файл есть», притворяясь, что означает
    «критерий сошёлся».
    """
    (tmp_path / "result.json").write_text('{"status": "broken"}', encoding="utf-8")

    verdict = observe_success_check(_BURN_IN_CHECK, tmp_path)

    assert verdict["verdict"] != "verified", (
        "критерий требовал status=ready, а файл говорит broken — "
        "проверялось только существование файла"
    )


def test_a_criterion_cannot_point_outside_the_workspace(tmp_path: Path) -> None:
    """След снаружи копии не засчитывает цель внутри неё.

    Без этого критерий сам по себе становится способом расширить область
    наблюдения: `../..` уводит проверяющего к чужому файлу, который положил
    кто угодно.
    """
    inside = tmp_path / "workspace" / "copy"
    inside.mkdir(parents=True)
    # Куда УВОДИТ путь: copy/tmp/../.. это workspace, то есть на уровень выше
    # объявленной копии. Именно там и кладётся чужой след.
    outside = tmp_path / "workspace" / "outside" / "result.json"
    outside.parent.mkdir(parents=True)
    outside.write_text('{"status": "ready"}', encoding="utf-8")
    assert (inside / "tmp/../../outside/result.json").resolve() == outside.resolve()

    verdict = observe_success_check(
        "готово, когда tmp/../../outside/result.json содержит status=ready", inside
    )

    assert verdict["verdict"] != "verified", (
        "критерий увёл проверяющего за пределы рабочей копии"
    )


def test_a_criterion_without_a_content_claim_still_checks_existence(
    tmp_path: Path
) -> None:
    """Сужения не произошло: критерий без пары по-прежнему проверяет наличие."""
    (tmp_path / "plan.md").write_text("черновик", encoding="utf-8")

    assert observe_success_check("создан plan.md", tmp_path)["verdict"] == "verified"
    assert observe_success_check("создан other.md", tmp_path)["verdict"] == "missing"


# --------------------------------------------------------------------------- #
# 2. Контракт доезжает: выбор -> кампания -> исполнитель -> журнал             #
# --------------------------------------------------------------------------- #

class _Pick:
    """Форма `CharterGoalReport` в той части, которую читает пейсер."""

    status = "proposed"
    goal = "создать result.json с признаком готовности"
    charter_quote = "агент доводит дело до наблюдаемого следа"
    why_now = "burn-in"
    success_check = _BURN_IN_CHECK
    reason = ""


def test_goal_contract_reaches_execution(tmp_path: Path, monkeypatch: Any) -> None:
    """Красный свидетель: критерий обязан дойти до КОНФИГА ИСПОЛНИТЕЛЯ.

    Проверяется весь шов, а не намерение: выбор цели -> кампания -> сборка
    `AutonomousRuntimeConfig`. Прежде на последнем участке `success_check`
    не существовало как поля вовсе.
    """
    from core import campaign_io

    seen: dict[str, Any] = {}

    class _Runtime:
        def __init__(self, agent: Any, **kwargs: Any) -> None:
            pass

        def run(self, config: AutonomousRuntimeConfig) -> Any:
            seen["config"] = config
            return SimpleNamespace(
                tasks=[], status="completed", stop_reason="",
                to_dict=dict, semantic_result=lambda: ("empty", False),
            )

    monkeypatch.setattr(
        "core.autonomous_runtime.AutonomousRuntime", _Runtime, raising=True
    )

    action = SimpleNamespace(
        action="study_external_source", title="t", severity="low", priority=1,
        risk="read_only", grounds="operator_goal", decided_by="test",
        next_check_at=None, reason="", evidence=(),
    )
    campaign_io._default_execute_action(
        agent=SimpleNamespace(log=None),
        workspace=tmp_path,
        action=action,
        config=CampaignConfig(goal=_Pick.goal, success_check=_BURN_IN_CHECK,
                              dry_run=True),
    )

    assert seen["config"].goal_success_check == _BURN_IN_CHECK, (
        "критерий цели не доехал до исполнителя: судить выполнение нечем"
    )


def test_the_cycle_record_keeps_the_criterion(tmp_path: Path) -> None:
    """Журнал цикла обязан нести критерий: иначе «сошлось ли» непроверяемо
    после прогона, а прогон длится десять часов без человека."""
    from core.campaign import run_campaign
    from core.campaign_ledger import CampaignLedger
    from core.campaign_types import CampaignActionOutcome

    def _gather(agent, workspace, approval_inbox, goal="", exhausted_actions=frozenset()):
        return {"action": SimpleNamespace(
            action="propose_engineering_task", title="one thing", severity="low",
            priority=10, risk="reversible", grounds="operator_goal",
            decided_by="test", next_check_at=None, reason="",
        )}

    def _execute(*, agent, workspace, action, config, approval_inbox=None):
        assert config.success_check == _BURN_IN_CHECK, (
            "исполнитель обязан слышать критерий ТЕКУЩЕЙ цели"
        )
        return CampaignActionOutcome(result="completed", work_done=True,
                                     subject="s", llm_calls_spent=1)

    ledger_path = tmp_path / "data" / "campaign_ledger.jsonl"
    result = run_campaign(
        CampaignConfig(goal=_Pick.goal, success_check=_BURN_IN_CHECK,
                       max_cycles=1, dry_run=False),
        agent=SimpleNamespace(log=None),
        workspace=str(tmp_path),
        gather_signals=_gather,
        execute_action=_execute,
        ledger=CampaignLedger(path=ledger_path),
        now_fn=lambda: datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc),
    )

    assert result.records[0].success_check == _BURN_IN_CHECK
    row = json.loads(ledger_path.read_text(encoding="utf-8").splitlines()[0])
    assert row["success_check"] == _BURN_IN_CHECK


def test_a_switched_goal_brings_its_own_criterion(tmp_path: Path) -> None:
    """Смена цели внутри прогона несёт НОВЫЙ критерий, а не прежний.

    Иначе после первой же смены исполнение судилось бы по чужой мерке — это
    хуже, чем отсутствие мерки: неверная проверка выглядит как проверка.
    """
    from core.campaign import run_campaign
    from core.campaign_ledger import CampaignLedger
    from core.campaign_types import CampaignActionOutcome

    def _gather(agent, workspace, approval_inbox, goal="", exhausted_actions=frozenset()):
        return {"action": SimpleNamespace(
            action="propose_engineering_task", title="one thing", severity="low",
            priority=10, risk="reversible", grounds="operator_goal",
            decided_by="test", next_check_at=None, reason="",
        )}

    seen: list[tuple[str, str]] = []

    def _execute(*, agent, workspace, action, config, approval_inbox=None):
        seen.append((config.goal, config.success_check))
        return CampaignActionOutcome(result="completed", work_done=True,
                                     subject="the-only-subject", llm_calls_spent=1)

    second = SimpleNamespace(goal="вторая цель", success_check="создан docs/second.md")

    run_campaign(
        CampaignConfig(goal="первая цель", success_check="создан docs/first.md",
                       max_cycles=8, max_idle_streak=2, dry_run=False),
        agent=SimpleNamespace(log=None),
        workspace=str(tmp_path),
        gather_signals=_gather,
        execute_action=_execute,
        ledger=CampaignLedger(),
        next_goal=lambda: second,
        now_fn=lambda: datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc),
    )

    assert ("первая цель", "создан docs/first.md") in seen
    assert ("вторая цель", "создан docs/second.md") in seen


def test_a_plain_string_goal_source_still_works(tmp_path: Path) -> None:
    """Обратная совместимость: источник целей вправе отдавать строку.

    Четыре точки входа и три десятка тестов зовут смену цели строкой. Новое
    поле не имеет права сделать их ложью — строка означает «критерий не
    назван», и это честное состояние, а не ошибка.
    """
    from core.campaign import run_campaign
    from core.campaign_ledger import CampaignLedger
    from core.campaign_types import CampaignActionOutcome

    def _gather(agent, workspace, approval_inbox, goal="", exhausted_actions=frozenset()):
        return {"action": SimpleNamespace(
            action="propose_engineering_task", title="one thing", severity="low",
            priority=10, risk="reversible", grounds="operator_goal",
            decided_by="test", next_check_at=None, reason="",
        )}

    seen: list[tuple[str, str]] = []

    def _execute(*, agent, workspace, action, config, approval_inbox=None):
        seen.append((config.goal, config.success_check))
        return CampaignActionOutcome(result="completed", work_done=True,
                                     subject="the-only-subject", llm_calls_spent=1)

    run_campaign(
        CampaignConfig(goal="первая цель", success_check="создан docs/first.md",
                       max_cycles=8, max_idle_streak=2, dry_run=False),
        agent=SimpleNamespace(log=None),
        workspace=str(tmp_path),
        gather_signals=_gather,
        execute_action=_execute,
        ledger=CampaignLedger(),
        next_goal=lambda: "вторая цель",
        now_fn=lambda: datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc),
    )

    assert ("вторая цель", "") in seen


def test_the_pacer_hands_the_whole_report_to_the_campaign(tmp_path: Path, monkeypatch: Any) -> None:
    """Пейсер обязан отдавать кампании ОТЧЁТ, а не строку.

    Это и есть место, где контракт терялся: `_pick_next_goal` знал
    `success_check` и выбрасывал его.
    """
    import agent_tick

    monkeypatch.setattr(agent_tick, "_charter_goal_router",
                        lambda ws: SimpleNamespace(for_role=lambda role: object()))
    monkeypatch.setattr("core.charter_goal.propose_charter_goal",
                        lambda llm, ws: _Pick(), raising=True)

    captured: dict[str, Any] = {}

    class _Result:
        status = "completed"
        stop_reason = ""
        cycles_run = 1
        totals: dict = {}  # noqa: RUF012 — подделка живёт один тест

        @staticmethod
        def user_summary() -> str:
            return "fake"

    def _fake_run_campaign(config, **kwargs):
        captured["config"] = config
        captured["next_goal"] = kwargs.get("next_goal")
        return _Result()

    agent_tick.run_paced_campaign(
        tmp_path,
        dry_run=True,
        goal=_Pick.goal,
        success_check=_BURN_IN_CHECK,
        max_cycles=1,
        charter_goals=True,
        heartbeat_fn=lambda ws, payload: None,
        run_campaign_fn=_fake_run_campaign,
        build_agent_fn=lambda ws: object(),
    )

    assert captured["config"].success_check == _BURN_IN_CHECK, (
        "стартовая цель приехала в кампанию без своего критерия"
    )
    handed = captured["next_goal"]()
    assert getattr(handed, "success_check", "") == _BURN_IN_CHECK, (
        "смена цели отдаёт строку: критерий следующей цели теряется"
    )


# --------------------------------------------------------------------------- #
# 3. Ответ — не выполнение                                                     #
# --------------------------------------------------------------------------- #

class _TalkativeAgent:
    """Агент, который ВСЕГДА рапортует об успехе и ничего не делает."""

    def __init__(self, workspace: Path, *, writes: str = "") -> None:
        self.workspace = workspace
        self.writes = writes
        self.policy = SimpleNamespace(blocked_tools=frozenset())
        self.last_answer_was_clarification = False
        self.last_replan_exhausted = False

    def run(self, *, user_question: str) -> str:
        if self.writes:
            target = self.workspace / self.writes
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('{"status": "ready", "value": 42}', encoding="utf-8")
        return "Готово: файл result.json создан, status=ready."


def _report(workspace: Path, *, writes: str = "") -> Any:
    agent = _TalkativeAgent(workspace, writes=writes)
    runtime = AutonomousRuntime(agent, workspace=workspace)
    task = AutonomousTask(kind="goal", description="создай result.json")
    return runtime._task_goal(task, AutonomousRuntimeConfig(
        goal="создать result.json",
        goal_success_check=_BURN_IN_CHECK,
        dry_run=True,
        include_tests=False,
    ))


def test_answer_is_not_execution(tmp_path: Path) -> None:
    """Красный свидетель: рассказ об успехе без следа в мире.

    Модель утверждает, что файл создан. Файла нет. До починки задача уезжала
    `done` — и кампания считала цикл полезным.
    """
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    report = _report(tmp_path)

    assert report.status == "inconclusive", (
        "ответ модели принят за выполнение: следа в мире нет"
    )
    assert report.details["success_check_verdict"] == "missing"
    assert report.details["success_check_missing"] == ["result.json"]
    # Ответ не выбрасывается: его надо видеть, чтобы понять, ЧЕМ соврали.
    assert "result.json" in report.details["answer"]


def test_a_real_artifact_makes_the_task_done(tmp_path: Path) -> None:
    """Обратная сторона: сделанное дело засчитывается — по миру, не по словам."""
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    report = _report(tmp_path, writes="result.json")

    assert report.status == "done"
    assert report.details["success_check_verdict"] == "verified"


def test_an_unverifiable_criterion_says_so_out_loud(tmp_path: Path) -> None:
    """Критерий без наблюдаемого следа не превращается ни в «да», ни в «нет».

    Прежнее поведение сохраняется (непустой ответ — `done`), но запись
    исполнения честно говорит, что проверка не проводилась.
    """
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    agent = _TalkativeAgent(tmp_path)
    runtime = AutonomousRuntime(agent, workspace=tmp_path)
    task = AutonomousTask(kind="goal", description="подумай о вечном")

    report = runtime._task_goal(task, AutonomousRuntimeConfig(
        goal="подумать", goal_success_check="агент стал мудрее",
        dry_run=True, include_tests=False,
    ))

    assert report.status == "done"
    assert report.details["success_check_verdict"] == "unverifiable"


def test_a_goal_without_a_criterion_keeps_the_old_behaviour(tmp_path: Path) -> None:
    """Пустой критерий — прежний путь. Починка не вводит новых отказов там,
    где вопрос вообще не ставился."""
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    agent = _TalkativeAgent(tmp_path)
    runtime = AutonomousRuntime(agent, workspace=tmp_path)
    task = AutonomousTask(kind="goal", description="что-нибудь")

    report = runtime._task_goal(task, AutonomousRuntimeConfig(
        goal="что-нибудь", dry_run=True, include_tests=False,
    ))

    assert report.status == "done"
    assert report.details["success_check_verdict"] == "unverifiable"


def test_a_config_cannot_be_built_positionally() -> None:
    """Порядок полей растущей настройки — не договор, и притворяться им не должен.

    Ревизия PR #333: `success_check` встал ВТОРЫМ полем `CampaignConfig`, и
    любой позиционный вызывающий с этого дня молча получал критерий успеха
    там, где передавал `max_cycles`. Молчаливая подмена смысла хуже падения:
    прогон продолжается, а настройка уже не та.
    """
    import pytest as _pytest

    from core.autonomous_runtime_types import AutonomousRuntimeConfig
    from core.campaign_types import CampaignConfig

    with _pytest.raises(TypeError):
        CampaignConfig("цель")
    with _pytest.raises(TypeError):
        AutonomousRuntimeConfig("цель")

    assert CampaignConfig(goal="цель").goal == "цель"


# ── признаки успеха: два замечания ревизии PR #334 ───────────────────────────
#
# Оба про одно: проверяющий обязан отвечать на критерий, а не на что-то
# похожее. Пока он отвечает похожим, «answer is not execution» остаётся
# наполовину закрытым — вердикт `verified` снова становится пересказом.


def test_a_claim_is_not_met_by_a_value_that_merely_starts_the_same(tmp_path):
    """I6. `status=ready` не вправе засчитываться на `status=ready_to_fail`.

    Ложное ПОДТВЕРЖДЕНИЕ, а не ложный отказ: у `_claim_pattern` не было
    границ, и значение-приставка считалось совпадением. Критерий «готово»
    закрывался файлом, буквально говорящим «готово провалиться».
    """
    from core.success_check import observe_success_check

    (tmp_path / "result.json").write_text(
        '{"status": "ready_to_fail"}', encoding="utf-8"
    )

    seen = observe_success_check(
        "result.json существует и содержит status=ready", tmp_path
    )

    assert seen["verdict"] == "missing", seen["reason"]


def test_a_claim_is_not_met_by_a_key_that_merely_ends_the_same(tmp_path):
    """I6, вторая половина: приставка у КЛЮЧА."""
    from core.success_check import observe_success_check

    (tmp_path / "result.json").write_text(
        '{"my_status": "ready"}', encoding="utf-8"
    )

    seen = observe_success_check(
        "result.json существует и содержит status=ready", tmp_path
    )

    assert seen["verdict"] == "missing", seen["reason"]


def test_a_claim_that_is_actually_met_is_still_met(tmp_path):
    """Сосед: границы не вправе сломать обычное совпадение.

    Три записи одного утверждения — критерий говорит об утверждении, а не о
    синтаксисе.
    """
    from core.success_check import observe_success_check

    for body in ('{"status": "ready"}', "status: ready", "status=ready"):
        (tmp_path / "result.json").write_text(body, encoding="utf-8")
        seen = observe_success_check(
            "result.json существует и содержит status=ready", tmp_path
        )
        assert seen["verdict"] == "verified", f"{body}: {seen['reason']}"


def test_a_claim_belongs_to_the_artifact_it_was_said_about(tmp_path):
    """S3. Утверждения применялись К КАЖДОМУ названному следу.

    Критерий «result.json содержит status=ready, и создан log.txt» требовал
    `status=ready` ещё и от `log.txt`. Ложный ОТКАЗ: честно выполненная цель
    объявлялась невыполненной, и агент принимался чинить работающее.
    """
    from core.success_check import observe_success_check

    (tmp_path / "result.json").write_text('{"status": "ready"}', encoding="utf-8")
    (tmp_path / "log.txt").write_text("работа шла\n", encoding="utf-8")

    seen = observe_success_check(
        "result.json содержит status=ready, и создан log.txt", tmp_path
    )

    assert seen["verdict"] == "verified", seen["reason"]


def test_the_artifact_that_owns_the_claim_still_has_to_meet_it(tmp_path):
    """Сосед к S3: привязка не вправе стать поблажкой.

    Тот же критерий, но `result.json` утверждения НЕ несёт. Отказ обязан
    остаться, иначе «привязали к своему» превратилось бы в «не проверяем».
    """
    from core.success_check import observe_success_check

    (tmp_path / "result.json").write_text('{"status": "broken"}', encoding="utf-8")
    (tmp_path / "log.txt").write_text("работа шла\n", encoding="utf-8")

    seen = observe_success_check(
        "result.json содержит status=ready, и создан log.txt", tmp_path
    )

    assert seen["verdict"] == "missing"
    assert "result.json" in seen["missing"]
    assert "log.txt" not in seen["missing"]


def test_a_book_that_was_there_before_the_goal_is_not_its_result(tmp_path: Path) -> None:
    """Кампания 2026-09-22, цикл 1: критерий «в книге Тонга найдено уравнение»
    называл ВХОДНУЮ книгу; она лежала с 15.09, и её существование засчиталось
    как работа, хотя ответ был о другом. След старше начала цели — не работа."""
    import os

    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    book = tmp_path / "library" / "book.txt"
    book.parent.mkdir(parents=True)
    book.write_text("Navier-Stokes ...", encoding="utf-8")
    os.utime(book, (1_700_000_000, 1_700_000_000))
    agent = _TalkativeAgent(tmp_path)
    runtime = AutonomousRuntime(agent, workspace=tmp_path)
    task = AutonomousTask(kind="goal", description="прочитай книгу")

    report = runtime._task_goal(task, AutonomousRuntimeConfig(
        goal="прочитать", goal_success_check="В library/book.txt найдено уравнение",
        dry_run=True, include_tests=False,
    ))

    assert report.status == "inconclusive", "существование входной книги засчитано как работа"
    assert report.details["success_check_verdict"] == "preexisting"

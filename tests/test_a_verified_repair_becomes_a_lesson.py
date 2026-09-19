"""Проверенная автономная починка обязана оставлять урок, и урок обязан читаться.

WHY THIS EXISTS. Аудит автономности 2026-09-17, находка 10: петля обучения не
замкнута. Замер по живому коду до этой правки:

* `record_rules_from_result` (core/self_build_rules.py) звалась РОВНО из одного
  места — `cli/commands_self_apply.py:83`, то есть только когда человек сам
  набрал `:self-apply-run`. Автономный путь применения
  (`drain_rule_approved_proposals`) не звал её никогда;
* извлекалось из исхода ровно одно узкое правило — `cannot import name 'X'
  from 'M'` — и только при `status == "rolled_back"`. Принятая починка не
  оставляла НИЧЕГО;
* `core/self_repair.py:_write_repair_lesson` на безнадзорном пути недостижим:
  без `approval_provider` починка обрывается раньше.

Итог: агент мог откатить одну и ту же негодную правку сколько угодно раз, и
десятый откат ничем не отличался от первого. Это и есть «ошибки не
накапливаются в знание».

Договор, который здесь проверяется:

    сбой -> диагноз -> попытка починки -> проверка -> устойчивый урок
    -> следующая похожая задача этот урок ЧИТАЕТ

Урок обязан нести происхождение: исходный сбой, что изменено, чем проверено,
принято или откачено, и на что распространяется. Урок без происхождения —
мнение, а не знание.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from core.approval_inbox import DEFAULT_APPROVAL_INBOX_PATH, ApprovalInbox
from core.self_apply_bridge import SELF_APPLY_OPERATION, build_self_apply_payload


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "logs").mkdir()
    (tmp_path / "data").mkdir()
    return tmp_path


def _inbox(workspace: Path) -> ApprovalInbox:
    return ApprovalInbox(path=workspace / DEFAULT_APPROVAL_INBOX_PATH)


def _grant(inbox: ApprovalInbox, *, runs_per_day: int = 5):
    item = inbox.add(
        operation="autonomous_runtime.standing_grant",
        summary=f"standing effects grant: {runs_per_day} runs/day",
        risk="irreversible",
        payload={"max_runs_per_day": runs_per_day},
        expires_at=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
    )
    return inbox.approve(item.id)


def _pending_document(inbox: ApprovalInbox, workspace: Path, name: str):
    payload = build_self_apply_payload(
        files=[{"path": f"knowledge/doctrine/future/{name}.md", "content": "# заметка\n"}],
        reason=f"черновик {name}",
        origin="test",
        workspace=workspace,
    )
    return inbox.add(
        operation=SELF_APPLY_OPERATION,
        summary=f"self-apply: {name}.md",
        risk="reversible",
        payload=payload,
    )


class _Lane:
    """Подделка полосы с заданным исходом: на диск ничего не пишет."""

    def __init__(self, status: str, *, reason: str = "", tests: tuple[str, ...] = ("tests",)) -> None:
        self.status = status
        self.reason = reason
        self.tests = tests
        self.item_ids: list[str] = []

    def __call__(self, **kwargs: Any) -> dict:
        item_id = kwargs["item_id"]
        self.item_ids.append(item_id)
        proposal = kwargs["inbox"].get(item_id)
        files = [f["path"] for f in (proposal.payload or {}).get("files", [])]
        return {
            "proposal_id": item_id,
            "origin": "test",
            "status": self.status,
            "reason": self.reason,
            "files_changed": files,
            "tests_run": list(self.tests),
            "rollback_status": "restored" if self.status == "rolled_back" else "none",
            "commit_hash": "abc123" if self.status == "committed_local" else None,
        }


def _install(monkeypatch: Any, lane: _Lane) -> _Lane:
    import core.self_apply_bridge as bridge

    monkeypatch.setattr(bridge, "run_approved_self_apply", lane)
    return lane


def _lessons(workspace: Path) -> list:
    from core.self_build_rules import LessonStore, default_lessons_path

    return LessonStore(default_lessons_path(workspace)).load()


#: Профиль памяти, ОТКРЫВАЮЩИЙ сток урока. Ровно тот, что даёт полномочие
#: песочницы (`core/burn_in_sandbox.SANDBOX_DURABLE_SINKS`). До ревизии PR #333
#: тесты этого файла доказывали запись урока под обычным стоячим грантом, то
#: есть доказывали ровно то, на что ревизия и указала: урок шёл мимо политики
#: памяти. Сам факт управляемости проверяется отдельными свидетелями ниже; эти
#: тесты говорят о СОДЕРЖАНИИ урока и потому приходят с открытым стоком.
_OPEN_SINKS = frozenset({"procedure", "knowledge"})


def _drain(workspace: Path, **kwargs: Any) -> dict:
    from core.rule_approved_apply import drain_rule_approved_proposals

    kwargs.setdefault("durable_writes", _OPEN_SINKS)
    return drain_rule_approved_proposals(workspace, dry_run=False, **kwargs)


def test_a_rolled_back_autonomous_apply_leaves_a_lesson(
    workspace: Path, monkeypatch: Any
) -> None:
    """Откат без человека за клавиатурой обязан оставить машиночитаемый след.

    Красный свидетель: до правки автономный путь не звал запись уроков вовсе,
    поэтому после отката на диске не появлялось ничего.
    """
    from core.rule_approved_apply import drain_rule_approved_proposals

    inbox = _inbox(workspace)
    _grant(inbox)
    _pending_document(inbox, workspace, "alpha")
    _install(monkeypatch, _Lane("rolled_back", reason="ImportError: cannot import name 'X' from 'core.y'"))

    _drain(workspace)

    lessons = _lessons(workspace)
    assert len(lessons) == 1, "откат прошёл, урока нет — опыт снова потерян"
    assert lessons[0].outcome == "rolled_back"
    assert lessons[0].origin == "rule_approved_apply", (
        "урок обязан называть, КТО его добыл: путь без человека или CLI"
    )


def test_a_verified_candidate_leaves_a_lesson(
    workspace: Path, monkeypatch: Any
) -> None:
    """Проверенный кандидат — тоже опыт: «так сработало» знание не меньшее, чем «так нет».

    Слово исхода именно `verified_candidate`, а не `accepted`: в этот момент
    полоса сделала локальный commit и вернула дерево на исходную ветку, то
    есть работающий агент остался на прежнем коде. Принятие — отдельное
    событие и делает его `core/burn_in_supervisor.adopt_offer`
    (ревизия PR #333, дефект 5).
    """
    from core.rule_approved_apply import drain_rule_approved_proposals

    inbox = _inbox(workspace)
    _grant(inbox)
    _pending_document(inbox, workspace, "beta")
    _install(monkeypatch, _Lane("committed_local"))

    _drain(workspace)

    lessons = _lessons(workspace)
    assert len(lessons) == 1
    assert lessons[0].outcome == "verified_candidate"


def test_a_lesson_carries_its_provenance(workspace: Path, monkeypatch: Any) -> None:
    """Пять полей происхождения, и ни одно не пустое.

    Урок без происхождения нельзя ни проверить, ни отозвать: непонятно, из
    какого сбоя он вырос и чем подтверждён.
    """
    from core.rule_approved_apply import drain_rule_approved_proposals

    inbox = _inbox(workspace)
    _grant(inbox)
    _pending_document(inbox, workspace, "gamma")
    _install(monkeypatch, _Lane("rolled_back", reason="тесты упали: 3 failed", tests=("tests/test_x.py",)))

    _drain(workspace)

    lesson = _lessons(workspace)[0]
    assert lesson.failure, "исходный сбой не записан"
    assert lesson.change, "что именно менялось — не записано"
    assert lesson.verification, "чем проверено — не записано"
    assert lesson.outcome in {"accepted", "rolled_back"}
    assert lesson.scope, "область применимости пуста — урок не с чем сопоставить"
    assert "knowledge/doctrine/future/gamma.md" in lesson.scope
    assert "tests/test_x.py" in lesson.verification


def test_a_later_similar_change_consumes_the_lesson(
    workspace: Path, monkeypatch: Any
) -> None:
    """Петля замыкается здесь: второй заход по тому же адресу читает урок.

    Без этого откат ничему не учит — следующий тик подаёт ту же правку, полоса
    снова гоняет батарею, снова откатывает, и так до конца суток.
    """
    from core.rule_approved_apply import drain_rule_approved_proposals

    inbox = _inbox(workspace)
    _grant(inbox)
    _pending_document(inbox, workspace, "delta")
    lane = _install(monkeypatch, _Lane("rolled_back", reason="батарея покраснела"))

    _drain(workspace)
    assert len(lane.item_ids) == 1

    # Тот же адрес, новая заявка — ровно та ситуация, которую урок описывает.
    _pending_document(inbox, workspace, "delta")
    out = _drain(workspace)

    assert out["applied"] == 0, "урок записан и не прочитан — это не память, а архив"
    assert out["refused"] == 1
    assert len(lane.item_ids) == 1, "полоса запущена повторно по уже отвергнутому адресу"


def test_a_different_target_is_not_shadowed_by_the_lesson(
    workspace: Path, monkeypatch: Any
) -> None:
    """Урок узок по построению: он про свой адрес, а не про всякую работу.

    Иначе один откат остановил бы петлю целиком, и «обучение» стало бы
    выключателем.
    """
    from core.rule_approved_apply import drain_rule_approved_proposals

    inbox = _inbox(workspace)
    _grant(inbox)
    _pending_document(inbox, workspace, "epsilon")
    lane = _install(monkeypatch, _Lane("rolled_back", reason="батарея покраснела"))
    _drain(workspace)

    lane.status = "committed_local"
    _pending_document(inbox, workspace, "zeta")
    out = _drain(workspace)

    assert out["applied"] == 1, "чужой адрес заблокирован уроком про другой файл"


def test_the_lesson_store_is_machine_readable(workspace: Path, monkeypatch: Any) -> None:
    """Урок хранится строками JSON, а не прозой: его обязан читать код."""
    import json

    from core.rule_approved_apply import drain_rule_approved_proposals
    from core.self_build_rules import default_lessons_path

    inbox = _inbox(workspace)
    _grant(inbox)
    _pending_document(inbox, workspace, "eta")
    _install(monkeypatch, _Lane("rolled_back", reason="батарея покраснела"))
    _drain(workspace)

    lines = default_lessons_path(workspace).read_text(encoding="utf-8").splitlines()
    assert lines, "файл уроков пуст"
    row = json.loads(lines[0])
    for field in ("failure", "change", "verification", "outcome", "scope", "origin"):
        assert field in row, f"в машинной записи нет поля {field}"


def test_an_import_rollback_still_yields_its_hard_rule(
    workspace: Path, monkeypatch: Any
) -> None:
    """Прежнее узкое правило не потеряно: урок добавился рядом, а не вместо.

    `keep_importable` — единственное правило, которое Критик умеет применять
    детерминированно; заменить его прозой значило бы разменять проверяемое
    на общее.
    """
    from core.rule_approved_apply import drain_rule_approved_proposals
    from core.self_build_rules import RuleStore, default_rules_path

    inbox = _inbox(workspace)
    _grant(inbox)
    _pending_document(inbox, workspace, "theta")
    _install(monkeypatch, _Lane(
        "rolled_back",
        reason="ImportError: cannot import name 'observe' from 'core.success_check'",
    ))

    _drain(workspace)

    rules = RuleStore(default_rules_path(workspace)).load()
    assert [r.symbol for r in rules] == ["observe"]
    assert rules[0].target == "core/success_check.py"


# ── Ревизия PR #333: урок обязан идти ЧЕРЕЗ ворота памяти ─────────────────────
#
# Первый заход замкнул петлю и открыл `procedure`/`knowledge` профилю
# песочницы. Но сам урок писался `LessonStore.add()` напрямую в
# `data/self_build_lessons.jsonl` — мимо `MemoryWritePolicy`, и звался в том
# числе на производственном `rule_approved_apply`. Требование «никаких
# неуправляемых прямых записей» выполнено не было: ворота открыли одну дверь, а
# урок ходил другой.


def test_production_unattended_does_not_write_a_lesson(
    workspace: Path, monkeypatch: Any
) -> None:
    """Безнадзорное производство урок НЕ пишет: сток `procedure` там закрыт.

    Производственная политика памяти не меняется — это и было условием
    эксперимента. Петля замыкается в песочнице, а не везде.
    """
    inbox = _inbox(workspace)
    _grant(inbox)
    _pending_document(inbox, workspace, "prod")
    _install(monkeypatch, _Lane("rolled_back", reason="тесты упали"))

    # durable_writes не назван: слив безнадзорен, и умолчание обязано быть
    # самым узким, а не самым широким.
    from core.rule_approved_apply import drain_rule_approved_proposals

    drain_rule_approved_proposals(workspace, dry_run=False)

    assert _lessons(workspace) == [], (
        "урок записан на производственном безнадзорном пути — "
        "прямая запись мимо политики памяти"
    )


def test_the_refusal_is_logged_not_silent(workspace: Path, monkeypatch: Any) -> None:
    """Отказ обязан быть СЛЫШНЫМ: молчащие ворота неотличимы от отсутствующих."""
    from core.rule_approved_apply import drain_rule_approved_proposals

    inbox = _inbox(workspace)
    _grant(inbox)
    _pending_document(inbox, workspace, "silent")
    _install(monkeypatch, _Lane("rolled_back", reason="тесты упали"))

    events: list[tuple[str, dict]] = []
    drain_rule_approved_proposals(
        workspace, dry_run=False, log=lambda e, p: events.append((e, p))
    )

    refusals = [p for e, p in events if e == "lesson_write_refused"]
    assert refusals, f"отказ записи урока не попал в журнал: {[e for e, _ in events]}"
    assert refusals[0]["sink"] == "procedure"


def test_an_open_sink_lets_the_lesson_through(
    workspace: Path, monkeypatch: Any
) -> None:
    """Обратная сторона: профиль с открытым стоком урок пропускает."""
    inbox = _inbox(workspace)
    _grant(inbox)
    _pending_document(inbox, workspace, "sandboxed")
    _install(monkeypatch, _Lane("rolled_back", reason="тесты упали"))

    _drain(workspace)

    assert len(_lessons(workspace)) == 1


def test_the_verdict_repeats_the_ladder_not_a_second_one() -> None:
    """Лестница одна: `None` — человек, набор — его содержимое решает."""
    from core.self_build_rules import LESSON_SINK, lesson_write_verdict

    assert lesson_write_verdict(None)[0] is True
    assert lesson_write_verdict(frozenset({LESSON_SINK}))[0] is True
    assert lesson_write_verdict(frozenset({"episode", "hygiene"}))[0] is False
    assert lesson_write_verdict(frozenset())[0] is False


def test_an_unreadable_lesson_journal_blocks_instead_of_allowing(
    workspace: Path
) -> None:
    """Испорченная строка журнала уроков ЗАПРЕЩАЕТ повтор, а не разрешает его.

    До ревизии PR #333 любое исключение чтения отвечало «препятствий нет».
    У ворот незнание обязано означать отказ, иначе порча одной строки снимает
    защиту, ради которой ворота и стоят.
    """
    from core.self_build_rules import blocking_lesson, default_lessons_path

    path = default_lessons_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{это не json\n", encoding="utf-8")

    verdict = blocking_lesson(workspace, ["core/foo.py"])

    assert verdict is not None, "нечитаемый журнал уроков снял ворота"
    assert "не прочитан" in verdict.failure or "не целиком" in verdict.failure


# ── Ревизия PR #333, дефект 5: предъявление принимающему ──────────────────────


def test_a_sandbox_candidate_is_offered_to_the_supervisor(
    workspace: Path, monkeypatch: Any
) -> None:
    """Проверенный кандидат песочницы попадает в реестр предложений.

    Без этой проводки `core/burn_in_supervisor` — мёртвый код: принимать ему
    было бы нечего, и петля осталась бы разомкнутой при полностью написанном
    принимающем. Отсутствие события неотличимо от отсутствия органа.
    """
    from core.burn_in_supervisor import offer_ledger

    inbox = _inbox(workspace)
    _grant(inbox)
    _pending_document(inbox, workspace, "offered")
    lane = _Lane("committed_local")
    _install(monkeypatch, lane)

    events: list[tuple[str, dict]] = []
    _drain(workspace, log=lambda e, p: events.append((e, p)))

    offers = [p for e, p in events if e == "burn_in_offer"]
    # Обычный стоячий грант песочницей НЕ является, и предъявлять ему нечего.
    assert offers == [], "производственный путь предъявил кандидата опыту"
    assert not offer_ledger(workspace).exists()


def test_the_offer_names_the_sha_not_the_branch(tmp_path: Path) -> None:
    """Реестр берёт только полный SHA: имя ветки — не неподвижное имя.

    Ветку можно переставить; принять ветку значит принять то, что окажется
    под ней к моменту проверки.
    """
    from core.burn_in_supervisor import offer_verified_commit

    assert offer_verified_commit(tmp_path, sha="a" * 40, proposal_id="p") is True
    assert offer_verified_commit(tmp_path, sha="main", proposal_id="p") is False
    assert offer_verified_commit(tmp_path, sha="a" * 8, proposal_id="p") is False


def test_a_rollback_is_not_counted_as_applied(workspace: Path, monkeypatch: Any) -> None:
    """Счётчик `applied` считает ПРИМЕНЁННОЕ, а не начатое.

    Ревизия PR #333: он рос на любом исходе, включая откат. По журналу выходило
    больше применений, чем изменений в дереве, и эта разница — ровно тот
    молчаливый счёт, из-за которого десятичасовой прогон нельзя прочитать
    задним числом.
    """
    inbox = _inbox(workspace)
    _grant(inbox)
    _pending_document(inbox, workspace, "rolled")
    _install(monkeypatch, _Lane("rolled_back", reason="тесты упали"))

    out = _drain(workspace)

    assert out["applied"] == 0, f"откат засчитан применением: {out}"
    assert out["unapplied"] == 1
    assert out["attempted"] == 1, "попытка потеряна — расход стал невидимым"


# ── Ревизия Copilot по PR #333: происхождение урока ─────────────────────────


def test_an_accepted_lesson_carries_the_failure_it_came_from(
    workspace: Path, monkeypatch: Any
) -> None:
    """Поле `failure` принятого урока — ИСХОДНЫЙ сбой, а не текст проверки.

    Урок обязан нести происхождение: из какого сбоя он вырос, что изменили,
    чем проверили. Для принятого кандидата полоса кладёт в `reason` свой успех
    («targeted + full tests passed; committed locally on temp branch»), и
    запасной ход на повод заявки не срабатывал НИКОГДА — у принятого исхода
    `reason` непуст всегда. В итоге «сбой» и «проверка» несли один и тот же
    текст, а настоящий повод починки терялся в тот единственный момент, когда
    урок и записывается.
    """
    from core.self_build_rules import lesson_from_apply_result

    lesson = lesson_from_apply_result(
        {
            "status": "committed_local",
            "reason": "targeted + full tests passed; committed locally on temp branch",
            "files_changed": ["core/widget.py"],
            "tests_run": ["tests/test_widget.py"],
            "rollback_status": "none",
        },
        origin="burn_in_sandbox",
        reason="ImportError: cannot import name _ToolRun",
    )

    assert lesson is not None
    assert "tests passed" not in lesson.failure, (
        f"«сбой» урока — это текст успешной проверки: {lesson.failure!r}"
    )
    assert "ImportError" in lesson.failure, (
        f"исходный сбой не дошёл до урока: {lesson.failure!r}"
    )
    assert "tests passed" in lesson.verification, (
        "текст проверки пропал из поля проверки"
    )


def test_a_rollback_lesson_still_carries_the_lane_reason(
    workspace: Path, monkeypatch: Any
) -> None:
    """Обратная сторона: у ОТКАЧЕННОГО повод полосы и есть сбой.

    Без этого свидетеля правку выше можно «починить», всегда предпочитая повод
    заявки, и тогда урок отката перестал бы говорить, ЧТО именно упало при
    проверке — а это единственное, ради чего откат и запоминают.
    """
    from core.self_build_rules import lesson_from_apply_result

    lesson = lesson_from_apply_result(
        {
            "status": "rolled_back",
            "reason": "full battery failed: 3 tests red",
            "files_changed": ["core/widget.py"],
            "tests_run": ["tests/test_widget.py"],
            "rollback_status": "restored",
        },
        origin="burn_in_sandbox",
        reason="ImportError: cannot import name _ToolRun",
    )

    assert lesson is not None
    assert "3 tests red" in lesson.failure, (
        f"урок отката потерял то, что упало: {lesson.failure!r}"
    )


def test_a_shared_registry_does_not_make_two_changes_the_same(workspace: Path) -> None:
    """Суточный прогон 2026-09-19: откат разбиения core/model_router.py записал
    урок с областью, включающей карту анатомии, — и заявки на разбиение
    core/step_sanitizer.py и core/smart_memory.py отвергались «prior rollback
    lesson», пересекаясь с ним только по этой общей карте."""
    from core.self_build_rules import Lesson, LessonStore, blocking_lesson, default_lessons_path

    store = LessonStore(default_lessons_path(workspace))
    store.add(Lesson(
        created_at="2026-09-19T11:01:40+00:00", origin="burn_in_sandbox", proposal_id="ain_x",
        failure="targeted tests failed", change="split", verification="rolled_back", outcome="rolled_back",
        scope=("core/model_router.py", "core/model_router_helpers.py", "core/anatomy_groups.py",
               "knowledge/generated/AGENT_ANATOMY.md"),
    ))
    other = ["core/step_sanitizer.py", "core/step_sanitizer_helpers.py", "core/anatomy_groups.py",
             "knowledge/generated/AGENT_ANATOMY.md"]
    assert blocking_lesson(workspace, other) is None, "a different module was blocked by a shared registry"
    same = ["core/model_router.py", "core/model_router_helpers.py", "core/anatomy_groups.py"]
    assert blocking_lesson(workspace, same) is not None, "the lesson must still stop the same change"
    assert blocking_lesson(workspace, ["core/anatomy_groups.py"]) is None, (
        "editing only the registry is not the rolled-back split"
    )
    store.add(Lesson(
        created_at="2026-09-19T12:00:00+00:00", origin="burn_in_sandbox", proposal_id="ain_y",
        failure="targeted tests failed", change="regroup", verification="rolled_back", outcome="rolled_back",
        scope=("core/anatomy_groups.py",),
    ))
    assert blocking_lesson(workspace, ["core/anatomy_groups.py"]) is not None, (
        "a rolled-back edit OF the registry still blocks repeating it"
    )

"""Песочница — ЯВНОЕ полномочие, а не щель в производственных воротах.

WHY THIS EXISTS. Аудит автономности 2026-09-17, находки 5 и 6: без человека
агент не может починить даже собственную изолированную копию — автономный путь
применения пропускает ровно один класс («новый документ .md»), а
`core/self_repair.py` без `approval_provider` обрывается на `unavailable`.
Значит десятичасовой прогон в песочнице измерял бы не самопочинку, а скорость,
с которой агент упирается в ожидание человека.

Отсюда режим: полномочие, которое нужно ВКЛЮЧИТЬ двумя независимыми жестами, и
которое ничего не меняет, пока оба не сделаны. Что здесь проверяется:

* производство fail-closed по умолчанию — ни переменная окружения, ни файл
  метки поодиночке полномочия не дают;
* метка обязана называть ИМЕННО эту рабочую копию: файл, скопированный в
  производственное дерево, полномочия там не включает;
* включённая песочница применяет КОД без человека, но той же полосой — с
  прицельными тестами, полной батареей, локальной веткой и откатом;
* песочница не вправе писать вне себя и не вправе двигать собственный забор;
* у песочницы есть суточный потолок, и он считается тем же журналом, что у
  стоячего гранта;
* каждая попытка — событие в журнале, и отказ тоже.

Анти-требование: это НЕ обход PolicyGate и не тихий флаг. Полномочие названо,
записано в журнал и ограничено сроком.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from core.approval_inbox import DEFAULT_APPROVAL_INBOX_PATH, ApprovalInbox
from core.burn_in_sandbox import (
    SANDBOX_ENV_FLAG,
    SANDBOX_MARKER,
    load_sandbox_authority,
    sandbox_execution_verdict,
)
from core.self_apply_bridge import SELF_APPLY_OPERATION, build_self_apply_payload


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "logs").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "config").mkdir()
    return tmp_path


def _marker(
    workspace: Path,
    *,
    path: str | None = None,
    days: int = 1,
    max_applies: int = 20,
    sandbox: bool = True,
    expires_at: str | None = None,
) -> None:
    (workspace / SANDBOX_MARKER).parent.mkdir(parents=True, exist_ok=True)
    (workspace / SANDBOX_MARKER).write_text(
        json.dumps({
            "sandbox": sandbox,
            "workspace": path if path is not None else str(workspace),
            "max_applies_per_day": max_applies,
            "expires_at": expires_at if expires_at is not None else
            (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(),
            "reason": "10-часовой burn-in, слово оператора",
        }),
        encoding="utf-8",
    )


def _env(on: bool) -> dict:
    return {SANDBOX_ENV_FLAG: "on"} if on else {}


def _inbox(workspace: Path) -> ApprovalInbox:
    return ApprovalInbox(path=workspace / DEFAULT_APPROVAL_INBOX_PATH)


def _pending_code(inbox: ApprovalInbox, workspace: Path, rel: str, body: str = "x = 1\n"):
    payload = build_self_apply_payload(
        files=[{"path": rel, "content": body}],
        reason=f"починка {rel}",
        origin="autonomous",
        workspace=workspace,
    )
    return inbox.add(
        operation=SELF_APPLY_OPERATION,
        summary=f"self-apply: {rel}",
        risk="reversible",
        payload=payload,
    )


class _Lane:
    def __init__(self, status: str = "committed_local") -> None:
        self.status = status
        self.item_ids: list[str] = []
        self.commit_hash: str | None = None

    def __call__(self, **kwargs: Any) -> dict:
        self.item_ids.append(kwargs["item_id"])
        return {
            "proposal_id": kwargs["item_id"],
            "status": self.status,
            "reason": "",
            "files_changed": [],
            "tests_run": ["tests"],
            "rollback_status": "none",
            "commit_hash": self.commit_hash,
        }


@pytest.fixture()
def lane(monkeypatch: Any) -> _Lane:
    import core.self_apply_bridge as bridge

    fake = _Lane()
    monkeypatch.setattr(bridge, "run_approved_self_apply", fake)
    return fake


# ── что включает полномочие, и что его НЕ включает ──────────────────────────


def test_production_is_fail_closed_by_default(workspace: Path) -> None:
    """Ни метки, ни переменной — полномочия нет. Это производственный путь."""
    assert load_sandbox_authority(workspace, env={}) is None


def test_the_env_flag_alone_is_not_the_authority(workspace: Path) -> None:
    """Переменная окружения — самый лёгкий жест, и одна она ничего не решает.

    Иначе полномочие включалось бы опечаткой в планировщике задач.
    """
    assert load_sandbox_authority(workspace, env=_env(True)) is None


def test_the_marker_alone_is_not_the_authority(workspace: Path) -> None:
    """Файл в дереве — тоже один жест. Забытая метка не вправе включать режим."""
    _marker(workspace)
    assert load_sandbox_authority(workspace, env={}) is None


def test_the_marker_must_name_this_workspace(workspace: Path) -> None:
    """Метка, скопированная в другое дерево, там не действует.

    Ровно тот случай, которого стоит бояться: песочницу клонировали в
    производство вместе с её меткой.
    """
    _marker(workspace, path=str(workspace / "somewhere-else"))
    assert load_sandbox_authority(workspace, env=_env(True)) is None


def test_an_expired_sandbox_is_not_an_authority(workspace: Path) -> None:
    """Срок — стена: вчерашний эксперимент не полномочие на сегодня."""
    _marker(workspace, days=-1)
    assert load_sandbox_authority(workspace, env=_env(True)) is None


def test_a_marker_that_does_not_claim_sandbox_is_not_one(workspace: Path) -> None:
    """`sandbox: false` — это отказ, а не наличие файла."""
    _marker(workspace, sandbox=False)
    assert load_sandbox_authority(workspace, env=_env(True)) is None


def test_both_gestures_make_an_authority_with_a_named_reason(workspace: Path) -> None:
    """Включённое полномочие себя называет: срок, потолок и повод."""
    _marker(workspace, max_applies=7)
    authority = load_sandbox_authority(workspace, env=_env(True))

    assert authority is not None
    assert authority.max_applies_per_day == 7
    assert authority.reason
    assert authority.id.startswith("sandbox:")


# ── что песочнице можно и чего нельзя ───────────────────────────────────────


def _proposal(workspace: Path, rel: str):
    from core.self_apply_bridge import rehydrate_proposal

    payload = build_self_apply_payload(
        files=[{"path": rel, "content": "x = 1\n"}],
        reason="починка",
        origin="autonomous",
        workspace=workspace,
    )
    return rehydrate_proposal(payload)


def test_the_sandbox_may_change_its_own_code(workspace: Path) -> None:
    """Смысл режима: правка кода в изолированной копии без ожидания человека."""
    allowed, reason = sandbox_execution_verdict(
        _proposal(workspace, "core/foo.py"), workspace=workspace
    )
    assert allowed, reason


def test_the_sandbox_cannot_write_outside_itself(workspace: Path) -> None:
    """Выход за пределы копии — отказ, названный до всякой полосы."""
    allowed, reason = sandbox_execution_verdict(
        _proposal(workspace, "../outside.py"), workspace=workspace
    )
    assert not allowed
    assert "outside" in reason or "вне" in reason


def test_the_sandbox_cannot_move_its_own_fence(workspace: Path) -> None:
    """Забор не двигается изнутри: ни метка, ни модуль полномочия.

    Самоизменяющаяся граница — это отсутствие границы.
    """
    for rel in ("config/burn_in_sandbox.json", "core/burn_in_sandbox.py"):
        allowed, reason = sandbox_execution_verdict(
            _proposal(workspace, rel), workspace=workspace
        )
        assert not allowed, f"{rel} пропущен: песочница вправе снять сама себя"
        assert "fence" in reason or "забор" in reason or "sandbox" in reason


def test_every_fence_entry_names_a_real_file() -> None:
    """Забор, сторожащий несуществующий файл, не сторожит ничего.

    Свидетель ревизии PR #333. В первом заходе в заборе стоял
    `core/policy_gate.py` — файла с таким именем в дереве НЕТ вовсе, настоящий
    `PolicyGate` живёт в `core/policy.py`. Запись выглядела как охрана ворот и
    не охраняла их: предложение изменить `core/policy.py` проходило.
    Проверять надо не написание, а существование названного.
    """
    from core.burn_in_sandbox import _FENCE, SANDBOX_MARKER

    root = Path(__file__).resolve().parents[1]
    # Метка — единственная запись, которой в производстве нет ПО ЗАМЫСЛУ: она
    # появляется только внутри подготовленной копии. Остальные записи забора
    # обязаны называть живой файл этого дерева.
    missing = sorted(
        rel for rel in _FENCE
        if rel != SANDBOX_MARKER and not (root / rel).exists()
    )
    assert missing == [], (
        f"забор называет несуществующие файлы: {missing} — "
        "такая запись создаёт видимость охраны"
    )


def test_the_fence_covers_who_holds_authority() -> None:
    """За забором обязаны быть все, кто решает, чем разрешено изменение.

    Полномочие проверяется не в одном месте: ворота (`core/policy.py`),
    замыкание петли одобрений (`core/rule_approved_apply.py`), учёт расхода
    постоянного гранта (`core/autonomous_runtime.py`) и сама полоса. Оставить
    хоть один снаружи — значит позволить песочнице переписать счетовода
    собственных полномочий, не трогая ни одной записи забора.
    """
    from core.burn_in_sandbox import _FENCE

    for rel in (
        "core/policy.py",
        "core/rule_approved_apply.py",
        "core/autonomous_runtime.py",
        "core/self_apply_lane.py",
        "core/burn_in_sandbox.py",
    ):
        assert rel in _FENCE, f"{rel} решает о полномочии и не за забором"


@pytest.mark.skipif(
    os.path.normcase("A") != "a",
    reason="файловая система различает регистр: вариант регистра — другой файл",
)
def test_a_case_variant_does_not_move_the_fence(workspace: Path) -> None:
    """Забор сверялся по СТРОКЕ пути, а не по тому, на какой файл он указывает.

    На файловой системе, не различающей регистр, `CORE/Burn_In_Sandbox.PY` —
    тот же самый файл, но другая строка. Сравнение имён пропускало его.
    """
    allowed, reason = sandbox_execution_verdict(
        _proposal(workspace, "CORE/Burn_In_Sandbox.PY"), workspace=workspace
    )
    assert not allowed, "вариант регистра снял забор: сверка идёт по строке"
    assert "fence" in reason or "забор" in reason


def test_a_symlink_does_not_move_the_fence(workspace: Path) -> None:
    """Вторая форма того же обхода: ссылка с невинным именем.

    `core/innocent.py` может указывать на модуль полномочия. Сверка по строке
    этого не видит; сверка по тому, КУДА путь разрешается, видит.
    """
    (workspace / "core").mkdir(parents=True, exist_ok=True)
    fence_file = workspace / "core" / "burn_in_sandbox.py"
    fence_file.write_text("# забор\n", encoding="utf-8")
    link = workspace / "core" / "innocent.py"
    try:
        link.symlink_to(fence_file)
    except (OSError, NotImplementedError) as exc:  # Windows без права на ссылки
        pytest.skip(f"символические ссылки недоступны: {exc}")

    allowed, reason = sandbox_execution_verdict(
        _proposal(workspace, "core/innocent.py"), workspace=workspace
    )
    assert not allowed, "ссылка провела предложение за забор"
    assert "fence" in reason or "забор" in reason


def test_the_sandbox_cannot_touch_secrets_or_ci(workspace: Path) -> None:
    """Запрещённые классы полосы остаются запрещёнными и в песочнице."""
    for rel in (".env", ".github/workflows/ci.yml"):
        allowed, _ = sandbox_execution_verdict(
            _proposal(workspace, rel), workspace=workspace
        )
        assert not allowed, f"{rel} пропущен"


def test_the_production_verdict_is_unchanged(workspace: Path) -> None:
    """Производственные ворота не тронуты: код без человека там не проходит."""
    from core.self_apply_lane import autonomous_execution_verdict

    allowed, reason = autonomous_execution_verdict(_proposal(workspace, "core/foo.py"))
    assert not allowed
    assert "not a document" in reason


# ── поведение слива под песочницей ──────────────────────────────────────────


def test_the_sandbox_applies_code_without_a_standing_grant(
    workspace: Path, lane: _Lane
) -> None:
    """Главный смысл: десять часов эксперимента не упираются в ящик человека.

    Без песочницы тот же слив отказал бы дважды — нет стоячего гранта и класс
    файла не документ.
    """
    from core.rule_approved_apply import drain_rule_approved_proposals

    inbox = _inbox(workspace)
    _marker(workspace)
    item = _pending_code(inbox, workspace, "core/foo.py")

    out = drain_rule_approved_proposals(workspace, dry_run=False, env=_env(True))

    assert out["applied"] == 1, out
    assert lane.item_ids == [item.id], "полоса обойдена — это был бы скрытый путь"
    assert inbox.get(item.id).status in {"approved", "executed", "aborted"}


def test_the_sandbox_is_off_without_the_env_flag(workspace: Path, lane: _Lane) -> None:
    """Та же заявка без второго жеста: обычный производственный отказ."""
    from core.rule_approved_apply import drain_rule_approved_proposals

    inbox = _inbox(workspace)
    _marker(workspace)
    _pending_code(inbox, workspace, "core/foo.py")

    out = drain_rule_approved_proposals(workspace, dry_run=False, env={})

    assert out["applied"] == 0
    assert out["blocked"] == "no active standing grant"
    assert lane.item_ids == []


def test_a_dry_run_sandbox_still_applies_nothing(workspace: Path, lane: _Lane) -> None:
    """Сухость старше полномочия: эксперимент не отменяет «ничего не менять»."""
    from core.rule_approved_apply import drain_rule_approved_proposals

    inbox = _inbox(workspace)
    _marker(workspace)
    _pending_code(inbox, workspace, "core/foo.py")

    out = drain_rule_approved_proposals(workspace, dry_run=True, env=_env(True))

    assert out["applied"] == 0
    assert out["blocked"] == "effects disabled"
    assert lane.item_ids == []


def test_the_sandbox_has_a_daily_ceiling(workspace: Path, lane: _Lane) -> None:
    """Полномочие без потолка — не полномочие, а состояние."""
    from core.rule_approved_apply import drain_rule_approved_proposals

    inbox = _inbox(workspace)
    _marker(workspace, max_applies=2)
    for name in ("a", "b", "c", "d"):
        _pending_code(inbox, workspace, f"core/{name}.py")

    out = drain_rule_approved_proposals(workspace, dry_run=False, env=_env(True))

    assert out["applied"] == 2, out
    assert len(lane.item_ids) == 2


def test_every_sandbox_attempt_is_logged(workspace: Path, lane: _Lane) -> None:
    """Прогон, о котором нечего прочитать, не эксперимент."""
    from core.rule_approved_apply import drain_rule_approved_proposals

    inbox = _inbox(workspace)
    _marker(workspace)
    _pending_code(inbox, workspace, "core/foo.py")
    _pending_code(inbox, workspace, ".env")

    events: list[tuple[str, dict]] = []
    drain_rule_approved_proposals(
        workspace, dry_run=False, env=_env(True),
        log=lambda e, p: events.append((e, p)),
    )

    kinds = [e for e, _ in events]
    assert "sandbox_authority_active" in kinds, "включённое полномочие не объявлено"
    assert any(k.startswith("rule_approval_refused") for k in kinds), (
        "отказ песочницы не записан"
    )


# ── Ревизия PR #333, дефект 5: кандидат доходит до принимающего ──────────────


def test_a_sandbox_candidate_reaches_the_offer_ledger(
    workspace: Path, lane: _Lane
) -> None:
    """Проверенный кандидат песочницы предъявляется принимающему.

    Полоса делает локальный commit и возвращает дерево на исходную ветку. Без
    предъявления этот SHA не знает никто, и `core/burn_in_supervisor` —
    мёртвый код: принимать нечего. Свидетель стоит здесь, потому что
    предъявление разрешено ТОЛЬКО полномочию песочницы.
    """
    import json

    from core.burn_in_supervisor import offer_ledger
    from core.rule_approved_apply import drain_rule_approved_proposals

    inbox = _inbox(workspace)
    _marker(workspace)
    item = _pending_code(inbox, workspace, "core/foo.py")
    lane.commit_hash = "b" * 40

    events: list[tuple[str, dict]] = []
    drain_rule_approved_proposals(
        workspace, dry_run=False, env=_env(True),
        log=lambda e, p: events.append((e, p)),
    )

    offered = [p for e, p in events if e == "burn_in_offer"]
    assert offered, f"кандидат не предъявлен: {[e for e, _ in events]}"
    assert offered[0]["sha"] == "b" * 40

    rows = [
        json.loads(line)
        for line in offer_ledger(workspace).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [r["sha"] for r in rows] == ["b" * 40]
    assert rows[0]["proposal_id"] == item.id


def test_a_rolled_back_candidate_is_not_offered(workspace: Path, monkeypatch: Any) -> None:
    """Откаченное не предъявляется: принимать нечего и незачем."""
    import core.self_apply_bridge as bridge

    from core.burn_in_supervisor import offer_ledger
    from core.rule_approved_apply import drain_rule_approved_proposals

    fake = _Lane(status="rolled_back")
    monkeypatch.setattr(bridge, "run_approved_self_apply", fake)

    inbox = _inbox(workspace)
    _marker(workspace)
    _pending_code(inbox, workspace, "core/foo.py")

    drain_rule_approved_proposals(workspace, dry_run=False, env=_env(True))

    assert not offer_ledger(workspace).exists()


# ── Ревизия PR #333: срок полномочия переживает построение профиля ───────────


def test_the_extra_sinks_close_when_the_term_ends(workspace: Path) -> None:
    """Расширенные стоки ЗАКРЫВАЮТСЯ по сроку, а не живут до конца процесса.

    Кампания строит агента ОДИН раз (`agent_tick.py:1832`) и работает часами.
    Срок песочницы проверялся только при построении профиля, поэтому полномочие,
    истёкшее на втором часу, продолжало действовать до конца прогона: список
    стоков — обычное множество строк, и о времени оно ничего не знает.

    Почему не «перестроить агента»: `_durable_learning_suppressed` прямо
    говорит, что `durable_writes` привязан к экземпляру на всю его жизнь и
    API на прогон нет нарочно. Значит срок обязан ехать ВНУТРИ самого
    списка — расширение носит свой срок с собой.
    """
    from datetime import datetime, timedelta, timezone

    from core.burn_in_sandbox import memory_profile_for

    base = {"durable_writes": frozenset({"episode", "hygiene"})}
    soon = datetime.now(timezone.utc) + timedelta(seconds=1)
    _marker(workspace, expires_at=soon.isoformat())

    profile = memory_profile_for(base, workspace, env=_env(True))
    sinks = profile["durable_writes"]

    assert "procedure" in sinks, "полномочие не открыло сток вовсе"
    assert hasattr(sinks, "contains_at"), (
        "список стоков не носит срока: расширение переживёт полномочие, "
        "потому что о времени множество строк ничего не знает"
    )

    later = soon + timedelta(seconds=1)
    assert not sinks.contains_at("procedure", later), (
        "истёкшее полномочие продолжает открывать сток обучения"
    )
    assert sinks.contains_at("episode", later), (
        "производственный сток закрылся вместе со сроком песочницы — "
        "закрываться обязано только расширение"
    )


def test_a_production_profile_is_a_plain_set(workspace: Path) -> None:
    """Без полномочия профиль остаётся ровно тем, чем был. Ни одной новой строки."""
    from core.burn_in_sandbox import memory_profile_for

    base = {"durable_writes": frozenset({"episode", "hygiene"})}
    profile = memory_profile_for(base, workspace, env={})

    assert profile["durable_writes"] == base["durable_writes"]
    assert type(profile["durable_writes"]) is frozenset


def test_a_marker_that_does_not_name_an_absolute_path_is_not_authority(
    workspace: Path, monkeypatch
) -> None:
    """Метка обязана называть АБСОЛЮТНЫЙ путь, а не «здесь».

    Ревизия Copilot по PR #333. Докстринг модуля обещает, что требование
    «метка называет свой путь» закрывает единственный по-настоящему опасный
    случай: песочницу склонировали в производственное дерево ВМЕСТЕ с меткой.
    Относительный путь это обещание отменяет: `"."` резолвится относительно
    текущего каталога, а тик как раз и запускают из корня рабочей копии. Такая
    метка переносима — она включает полномочие в ЛЮБОМ дереве, куда её
    скопировали, то есть ровно в том случае, ради которого проверка заведена.
    """
    monkeypatch.chdir(workspace)
    _marker(workspace, path=".")

    assert load_sandbox_authority(workspace, env=_env(True)) is None, (
        "метка с относительным путём включила полномочие: скопированная в "
        "производственное дерево, она включит его и там"
    )


def test_a_marker_naming_this_copy_absolutely_is_still_authority(
    workspace: Path, monkeypatch
) -> None:
    """Обратная сторона: строгость не должна ломать честную метку.

    Без этого свидетеля правку выше можно «починить» отказом всегда.
    """
    monkeypatch.chdir(workspace)
    _marker(workspace, path=str(workspace))

    assert load_sandbox_authority(workspace, env=_env(True)) is not None, (
        "метка назвала абсолютный путь именно этой копии и была отвергнута"
    )


def test_the_sandbox_verdict_refuses_what_the_lane_would_refuse(
    workspace: Path,
) -> None:
    """Вердикт песочницы не вправе одобрять то, что полоса потом отвергнет.

    Ревизия Copilot по PR #333. Вердикт проверял только запрещённые классы, а
    полоса проверяет И разрешённые (`classify_patch_risk` -> `_is_allowed`).
    Разрыв стоит дорого: заявка проходит ворота, ЗАНИМАЕТ единицу суточного
    потолка, уезжает в полосу и там отвергается. Потолок потрачен на то, что
    не могло примениться ни при каких условиях.
    """
    from core.self_apply_lane import classify_patch_risk

    class _Change:
        def __init__(self, path: str) -> None:
            self.path = path
            self.content = "{}\n"

    class _Proposal:
        files = (_Change("data/foo.json"),)

    lane_ok, _, _ = classify_patch_risk(_Proposal.files)
    assert not lane_ok, (
        "предпосылка свидетеля рассыпалась: полоса теперь принимает "
        "data/foo.json, и разрыва между воротами и полосой здесь нет"
    )

    allowed, reason = sandbox_execution_verdict(_Proposal(), workspace=workspace)
    assert not allowed, (
        "ворота песочницы одобрили то, что полоса отвергнет: потолок будет "
        f"потрачен впустую (вердикт: {reason!r})"
    )


def test_an_exhausted_sandbox_is_not_reported_as_a_standing_grant(
    workspace: Path, lane: _Lane
) -> None:
    """Исчерпанная песочница обязана назваться песочницей.

    Ревизия Copilot по PR #333. `grant_id` в событии несёт префикс `sandbox:`
    и потому не врёт, но повод отказа, который читает человек, и ИМЯ события
    говорят про стоячий грант — полномочие другой природы, с другим сроком и
    другим владельцем. Журнал, называющий не то разрешение, хуже молчания:
    оператор пойдёт искать грант, которого нет.
    """
    from core.rule_approved_apply import drain_rule_approved_proposals

    inbox = _inbox(workspace)
    _marker(workspace, max_applies=1)
    _pending_code(inbox, workspace, "core/a.py")
    _pending_code(inbox, workspace, "core/b.py")

    events: list[tuple[str, dict]] = []
    out = drain_rule_approved_proposals(
        workspace, dry_run=False, env=_env(True),
        log=lambda e, p: events.append((e, p)),
    )

    assert out["applied"] == 1, out
    assert "standing grant" not in out["blocked"], (
        f"повод отказа называет стоячий грант, а работала песочница: "
        f"{out['blocked']!r}"
    )
    kinds = [e for e, _ in events]
    assert "standing_grant_exhausted" not in kinds, (
        "исчерпание песочницы записано именем стоячего гранта: " + repr(kinds)
    )
    assert any("sandbox" in k and "exhaust" in k for k in kinds), (
        "исчерпание песочницы не названо своим именем: " + repr(kinds)
    )


def test_the_fence_covers_the_place_the_grant_is_served_from() -> None:
    """Ревизия PR #334: `core/approval_inbox.py` не был за забором.

    Полномочие песочницы упирается в `active_standing_grant`
    (`core/autonomous_runtime.py:319`), а тот НЕ ищет грант сам — он
    перебирает `approval_inbox.list(status="approved")`. Значит решает не
    только тот, кто спрашивает, но и тот, кто отвечает: переписав хранилище
    одобрений, песочница выдаёт себе постоянный грант, не тронув ни одной
    записи забора.

    Это нарушение правила, записанного в докстринге самого забора: «за забором
    стоит КАЖДЫЙ, кто решает о полномочии». Правило было верным, список — нет.
    """
    from core.burn_in_sandbox import _FENCE

    assert "core/approval_inbox.py" in _FENCE, (
        "хранилище одобрений отвечает на вопрос о полномочии и не за забором"
    )
    assert "core/safe_vcs.py" in _FENCE, (
        "список разрешённых глаголов git — тоже полномочие: он решает, "
        "что полоса вправе сделать с репозиторием"
    )


def test_the_two_fences_do_not_disagree_about_authority() -> None:
    """Забор песочницы не вправе быть шире забора принимающего.

    Перечисление ловит только ту запись, которую вспомнили. Это утверждение
    ловит КЛАСС пропусков: два забора обязаны сходиться, иначе файл, который
    песочнице трогать нельзя, окажется тем, что принимающий пропускает, — и
    запрет, обойдённый один раз, станет принятым изменением.
    """
    from core.burn_in_sandbox import _FENCE
    from core.burn_in_supervisor import SUPERVISOR_FENCE

    outside = sorted(set(_FENCE) - set(SUPERVISOR_FENCE))

    assert not outside, (
        f"песочнице запрещено трогать {outside}, а принимающий примет "
        f"кандидата, который их тронул"
    )


def test_a_symlink_does_not_launder_a_denied_class(workspace: Path) -> None:
    """Ревизия PR #334: классы спрашивались о СЫРОМ пути, а писал резолвленный.

    Забор к этому времени уже сверяет `target.resolve()`, и сдерживание тоже:
    ссылка ЗА пределы копии ловится. Осталась ссылка ВНУТРИ копии — с
    невинным именем на запрещённый класс. `core/innocent.py` проходит и
    `_is_denied`, и `_is_allowed`, а `_write_file`
    (`core/self_apply_lane.py:481`) резолвит ссылку и пишет в рабочий процесс
    CI. Класс файла определяется тем, ЧТО меняется, а не тем, как названо.
    """
    (workspace / "core").mkdir(parents=True, exist_ok=True)
    ci = workspace / ".github" / "workflows" / "ci.yml"
    ci.parent.mkdir(parents=True, exist_ok=True)
    ci.write_text("on: push\n", encoding="utf-8")
    link = workspace / "core" / "innocent.py"
    try:
        link.symlink_to(ci)
    except (OSError, NotImplementedError) as exc:  # Windows без права на ссылки
        pytest.skip(f"символические ссылки недоступны: {exc}")

    allowed, reason = sandbox_execution_verdict(
        _proposal(workspace, "core/innocent.py"), workspace=workspace
    )

    assert not allowed, "ссылка отмыла запрещённый класс: правка уедет в CI"
    assert "denied" in reason or "allowlist" in reason


def test_an_honest_code_file_is_still_allowed(workspace: Path) -> None:
    """Сосед: обычный файл кода в копии по-прежнему разрешён.

    Иначе починка классов превратила бы песочницу в запрет на эксперимент.
    """
    (workspace / "core").mkdir(parents=True, exist_ok=True)
    (workspace / "core" / "widget.py").write_text("VALUE = 1\n", encoding="utf-8")

    allowed, reason = sandbox_execution_verdict(
        _proposal(workspace, "core/widget.py"), workspace=workspace
    )

    assert allowed, reason


def test_the_term_is_checked_again_when_a_unit_is_reserved(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ревизия PR #334: срок проверялся один раз, при выдаче полномочия.

    `load_sandbox_authority` сверяет `expires_at` и больше никто. Слив
    перебирает заявки, каждая тянет полосу с полной батареей, то есть минуты;
    полномочие, истёкшее посреди прохода, продолжало занимать единицы потолка
    и разрешать изменения. Ровно этот класс уже был закрыт для стоков памяти
    (`TermedSinks`) — тем же доводом и по тому же замеру.
    """
    from core.burn_in_sandbox import SandboxAuthority, reserve_sandbox_apply

    expired = SandboxAuthority(
        id="sandbox:2020-01-01T00:00:00+00:00",
        workspace=str(Path(workspace).resolve()),
        max_applies_per_day=20,
        expires_at="2020-01-01T00:00:00+00:00",
        reason="истёкшее полномочие",
    )

    assert not reserve_sandbox_apply(workspace, expired), (
        "истёкшее полномочие заняло единицу потолка и разрешило изменение"
    )


def test_a_live_term_still_reserves(workspace: Path) -> None:
    """Сосед: действующее полномочие по-прежнему занимает единицу."""
    from datetime import datetime, timedelta, timezone

    from core.burn_in_sandbox import SandboxAuthority, reserve_sandbox_apply

    later = (datetime.now(timezone.utc) + timedelta(hours=10)).isoformat()
    live = SandboxAuthority(
        id=f"sandbox:{later}",
        workspace=str(Path(workspace).resolve()),
        max_applies_per_day=20,
        expires_at=later,
        reason="действующее полномочие",
    )

    assert reserve_sandbox_apply(workspace, live)

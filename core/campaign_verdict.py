"""Вердикт кампании: сошёлся ли ЕЁ критерий, и записан ли этот факт.

Зачем существует. `core/success_check.py` читает мир и говорит, найдены ли
названные критерием следы. Он был подключён к одиночной задаче
(`core/autonomous_runtime`) и НЕ подключён к кампании: `core/campaign.py`
вёз `success_check` в каждой строке реестра и ни разу по нему не судил.
Поэтому слова «достигнуто» в системе не существовало, и страж повторов
(`charter_goal._recent_goals`) запирал тему по факту «был цикл с работой» —
единственному факту, который вообще был записан.

Почему здесь появляется ЧЕТВЁРТОЕ слово. Замер живого реестра владельца
(232 строки, 31 несовпадающий критерий) перед правкой: 27 критериев →
`unverifiable`, 4 → `verified`, и все четыре ложны. Критерий гласил
«существует проверенная заявка, перечисляющая, что выделить из
core/smart_memory.py»; судья находил в мире `core/smart_memory.py` — файл,
лежащий там годами, — и объявлял цель достигнутой, тогда как заявок в той
кампании было ноль. Судья не различал ПРЕДМЕТ разговора и ПРОДУКТ работы.

Подключить его как есть значило бы записать в вечный журнал четыре ложных
«достигнуто». Разделитель наблюдаем и не требует модели: след, который
старше начала кампании, об этой кампании не свидетельствует.

* ``verified``     — все следы найдены И хотя бы один моложе начала прогона;
* ``preexisting``  — все найдены, но все старше прогона (НЕ успех);
* ``missing``      — хотя бы один назван и не найден;
* ``unverifiable`` — наблюдаемого следа не названо (в том числе когда
                     критерия не назвали вовсе: четыре точки входа задают
                     цель строкой, и 63 строки живого реестра пришли без него).

Чего здесь НЕТ намеренно. Никто на этот вердикт пока не опирается: страж
повторов не меняется. Сначала факт должен существовать и быть проверяемым.
Опираться на только что заведённое поле, у которого ещё нет истории, — это
тот же прыжок через доказательство, из-за которого появился `preexisting`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.state_integrity import append_state_jsonl_unlocked, state_file_lock
from core.success_check import observe_success_check

#: Единственный путь журнала. Второй писатель со своим путём — та же болезнь,
#: из-за которой читателю остановок пришлось сворачивать чужие строки.
VERDICT_RELPATH = "data/campaign_verdicts.jsonl"

#: Слова вердикта. Список закрыт: незнание, выданное за проверку, обязано
#: называться отдельно от проверки.
VERDICTS = ("verified", "preexisting", "missing", "unverifiable")

_UNSTATED = "критерий не назван: цель поставлена без проверки"


def _fresh_traces(
    named: tuple[str, ...] | list[str], workspace: Any, since: float | None,
) -> list[str]:
    """Следы, изменившиеся НЕ РАНЬШЕ начала прогона.

    Без `since` (начало неизвестно) свежим не считается ничего: выдумать
    свежесть хуже, чем её не знать.
    """
    if since is None:
        return []
    root = Path(workspace or ".")
    fresh: list[str] = []
    for relpath in named:
        try:
            mtime = (root / relpath).stat().st_mtime
        except OSError:
            continue
        if mtime >= since:
            fresh.append(relpath)
    return fresh


def judge_campaign(
    *, goal: str, success_check: str, workspace: Any, since: float | None = None,
) -> dict[str, Any]:
    """Сказать, сошёлся ли критерий кампании, и чем это наблюдается."""
    check = str(success_check or "").strip()
    observation = observe_success_check(check, workspace)
    named = [str(a) for a in observation.get("artifacts") or ()]
    missing = [str(m) for m in observation.get("missing") or ()]
    reason = str(observation.get("reason") or "")
    verdict = str(observation.get("verdict") or "unverifiable")
    fresh: list[str] = []
    if verdict == "verified":
        fresh = _fresh_traces(named, workspace, since)
        if not fresh:
            verdict = "preexisting"
            reason = (
                "следы найдены, но все старше начала прогона: "
                + ", ".join(named)
            )
    if not check:
        # Непоставленный критерий и поставленный, но непроверяемый, дают одно
        # слово `unverifiable` — и это правда: проверить нечем. Но причина у
        # них разная, и человеку она нужна разная.
        reason = _UNSTATED
    return {
        "goal": str(goal or ""),
        "success_check": check,
        "verdict": verdict,
        "reason": reason,
        "named_traces": named,
        "missing_traces": missing,
        "fresh_traces": fresh,
    }


def record_campaign_verdict(workspace: Any, verdict: dict[str, Any]) -> dict[str, Any]:
    """Положить вердикт в свой журнал. Реестр циклов не трогается.

    Его читатели (`_recent_goals`, `spent_units_by_action`, `summarise_ledger`)
    считают «строка = цикл», и строка не-цикла сломала бы каждого из них.
    """
    path = Path(workspace or ".") / VERDICT_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with state_file_lock(path):
        append_state_jsonl_unlocked(path, [dict(verdict)])
    return verdict


def judge_and_record(
    *, goal: str, success_check: str, workspace: Any, started_at: Any, ts: Any,
    stop_reason: str = "", cycles_run: int = 0, proposals: int = 0, artifacts: int = 0,
) -> tuple[dict[str, Any], str]:
    """Судить цель, назвать прогон и положить вердикт в журнал.

    Возвращает вердикт и текст ошибки записи (пустой, если записалось).
    Незаписанный вердикт НЕ валит прогон: работа кампании уже сделана, и
    терять её из-за недоступного файла — хуже, чем потерять вердикт. Но
    молчать об этом нельзя, поэтому ошибка возвращается наружу.
    """
    try:
        since = started_at.timestamp()
    except (AttributeError, OverflowError, OSError, ValueError):
        since = None
    verdict = judge_campaign(
        goal=goal, success_check=success_check, workspace=workspace, since=since,
    )
    # Чей это вердикт: без прогона его нельзя ни перепроверить, ни привязать
    # к строкам реестра. Считает судья, называет прогон.
    verdict.update({
        "stop_reason": str(stop_reason or ""),
        "cycles_run": int(cycles_run),
        "started_at": _isoformat(started_at),
        "ts": _isoformat(ts),
        "proposals": int(proposals),
        "artifacts": int(artifacts),
    })
    try:
        record_campaign_verdict(workspace, verdict)
    except OSError as exc:
        return verdict, str(exc)
    return verdict, ""


def _isoformat(moment: Any) -> str:
    try:
        return str(moment.isoformat())
    except AttributeError:
        return str(moment or "")


def verdict_summary_line(verdict: dict[str, Any]) -> str:
    """Строка для человека: слово вердикта и то, чем оно наблюдается."""
    word = str(verdict.get("verdict") or "unverifiable")
    missing = verdict.get("missing_traces") or []
    fresh = verdict.get("fresh_traces") or []
    tail = ""
    if word == "missing" and missing:
        tail = "  not found: " + ", ".join(str(m) for m in missing)
    elif word == "verified" and fresh:
        tail = "  made during the run: " + ", ".join(str(f) for f in fresh)
    elif word == "preexisting":
        tail = "  older than this run: " + ", ".join(
            str(n) for n in verdict.get("named_traces") or []
        )
    elif word == "unverifiable":
        tail = "  " + str(verdict.get("reason") or "")
    return f"goal_achieved={word}{tail}"

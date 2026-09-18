"""Писатель стоячего гранта: одна форма записи на все двери.

Читателя вынесли раньше и по названной причине: `active_standing_grant` живёт
в `core/autonomous_runtime.py` один на весь проект, потому что вторая проверка
разошлась бы с первой (класс H-26 проспективного аудита). Писатель до сих пор
жил в обработчике REPL-команды, и у него та же болезнь наоборот: вторая дверь
неизбежно завела бы заявку своей формы — с другим ключом границ или другой
операцией, — и читатель молча её не узнал бы. Поэтому форма записи здесь одна,
а дверей может быть сколько угодно.

Просьба и разрешение остаются ДВУМЯ событиями (MIR-117, правило B). Модуль даёт
их раздельно: `file_standing_grant` кладёт заявку, `open_standing_grant` кладёт
и выносит вердикт — но вердикт всегда назван по автору, и «unattributed» тут не
пишется никогда. Это тот же приём, которым правило `core/rule_approved_apply.py`
разрешает себе действовать: не отменить второе событие, а подписать его.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

#: Операция стоячего гранта. Её ЧИТАЕТ `core.autonomous_runtime`; расхождение
#: этой строки с читателем означало бы грант, которого рантайм не видит.
STANDING_GRANT_OPERATION = "autonomous_runtime.standing_grant"

#: Автор, которым подписан вердикт, выданный флагом. Не «unattributed» и не имя
#: автомата: флаг набирает человек, и подпись обязана говорить именно это.
COMMAND_LINE_ACTOR = "operator:agent_tick --standing-grant"

#: Срок по умолчанию. Тот же, что у REPL-команды: две двери, одна норма.
DEFAULT_GRANT_HOURS = 48


def _bounds(runs_per_day: Any, hours: Any) -> tuple[int, int]:
    """Границы обязательны и проверяются ДО любой записи.

    Ноль прогонов читатель понимает как «гранта нет», нулевой срок — как
    истёкший. Молча завести такую заявку хуже, чем отказать: ящик показывал бы
    право, которого не существует.
    """
    try:
        runs = int(runs_per_day)
        span = int(hours)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "границы гранта должны быть числами: прогонов в сутки и часов"
        ) from exc
    if runs <= 0 or span <= 0:
        raise ValueError(
            "границы обязательны: и прогонов в сутки, и часов должно быть "
            f"больше нуля (получено {runs} и {span})"
        )
    return runs, span


def _named(who: Any, field: str) -> str:
    """Имя автора обязательно и проверяется ДО любой записи.

    `ApprovalInbox._verdict` превращает пустого автора в «unattributed» —
    в слово, которым помечено разрешение, которого никто не давал. Модуль
    обещает, что по этим путям его не будет; держать обещание должен код,
    а не вежливость вызывающего.
    """
    name = str(who or "").strip()
    if not name:
        raise ValueError(
            f"{field} обязателен: неподписанная запись неотличима от той, "
            "которой никто не делал"
        )
    return name


def file_standing_grant(
    inbox: Any, *, runs_per_day: Any, hours: Any, requested_by: str,
) -> Any:
    """Положить заявку на стоячий грант. Кладёт, а не выдаёт."""
    runs, span = _bounds(runs_per_day, hours)
    asked_by = _named(requested_by, "проситель")
    expires_at = (
        datetime.now(timezone.utc) + timedelta(hours=span)
    ).isoformat()
    return inbox.add(
        operation=STANDING_GRANT_OPERATION,
        summary=(
            f"Стоячий грант на автономные прогоны с эффектами: "
            f"{runs} в сутки, {span} ч."
        ),
        risk="irreversible",
        reasons=(
            f"запрошен оператором: {asked_by}",
            "без гранта каждое срабатывание расписания упирается в ворота",
            f"границы: {runs} прогонов в сутки, срок {span} ч",
        ),
        payload={"max_runs_per_day": runs},
        requested_by=asked_by,
        expires_at=expires_at,
    )


def open_standing_grant(
    workspace: Any,
    *,
    runs_per_day: Any,
    hours: Any,
    actor: str,
    reason: str = "",
    inbox: Any = None,
) -> Any:
    """Положить заявку и открыть её одним действием оператора.

    Уступка названа вслух: оба шага оператора делаются одной командой. Защищает
    здесь не церемония из двух нажатий, а то, что переживает прогон, — границы
    обязательны, риск объявлен, срок конечен, обе записи лежат в долговечном
    ящике, и вердикт назван по автору.

    Вызывать это разрешено ТОЛЬКО разбору аргументов командной строки: автором
    записи становится человек, набравший флаг. Коду агента путь сюда закрыт —
    иначе автомат открывал бы право сам себе, и подпись стала бы ложью.
    """
    runs, span = _bounds(runs_per_day, hours)
    signed_by = _named(actor, "автор вердикта")
    if inbox is None:
        from core.approval_inbox import DEFAULT_APPROVAL_INBOX_PATH, ApprovalInbox

        inbox = ApprovalInbox(
            path=Path(workspace) / DEFAULT_APPROVAL_INBOX_PATH
        )
    item = file_standing_grant(
        inbox, runs_per_day=runs, hours=span, requested_by=signed_by,
    )
    return inbox.approve(
        item.id,
        reason=reason or (
            f"открыт оператором из точки входа: {runs} прогонов в сутки, "
            f"срок {span} ч"
        ),
        actor=signed_by,
    )


def open_grant_from_command_line(
    workspace: Any,
    *values: Any,
    out: Any = None,
) -> int:
    """Открыть грант флагом и сказать, что именно открыто.

    Отдельный выход, как у ``--status``: это административное действие, а
    не прогон. Печатает границы, срок и автора: оператор должен увидеть,
    что именно он выдал, а не только услышать «готово».

    Границы принимает ЦЕЛИКОМ и сама же судит их число. Прежде точка входа
    резала список до двух, и `--standing-grant 20 48 999` завершался нулём,
    открыв совсем другое право, чем набрал человек. Необратимое право —
    не место для догадок о намерении.

    Отказ возвращает ненулевой код и не кладёт заявки: молчаливый нуль
    при негодных границах прочитался бы в расписании как «грант есть».
    """
    stream = sys.stdout if out is None else out
    if not 1 <= len(values) <= 2:
        got = " ".join(str(v) for v in values) or "(ничего)"
        print(
            "Грант не открыт: ожидаются прогонов в сутки и, необязательно, "
            f"часов — не больше двух чисел (получено: {got})",
            file=stream,
        )
        return 2
    runs_per_day = values[0]
    hours = values[1] if len(values) == 2 else DEFAULT_GRANT_HOURS
    try:
        item = open_standing_grant(
            workspace,
            runs_per_day=runs_per_day,
            hours=hours,
            actor=COMMAND_LINE_ACTOR,
        )
    except ValueError as exc:
        print(f"Грант не открыт: {exc}", file=stream)
        return 2
    cap = int((item.payload or {}).get("max_runs_per_day") or 0)
    print(f"Стоячий грант открыт: {item.id}", file=stream)
    print(f"  {cap} прогонов в сутки, истекает {item.expires_at}", file=stream)
    print(f"  разрешил: {item.decided_by}", file=stream)
    return 0

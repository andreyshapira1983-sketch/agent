"""Ответ написала не та модель, которую выбрал маршрут."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: Метка, которой роутер помечает вызов, ушедший к запасному поставщику.
FAILOVER_PREFIX = "provider_failover:"
HEALTH_PREFIX = "provider_unhealthy:"

#: Человеческая часть ошибки провайдера. Внутри — repr словаря из SDK, и
#: сообщение живёт только там; если формат сменится, остаётся класс исключения.
_MESSAGE_RE = re.compile(r"'message':\s*'([^']{4,400})'")

#: Хвост ответа читает оператор, а не журнал: длинная простыня в нём вытеснит
#: то, ради чего он написан.
_MAX_REASON = 160

#: Неизменное начало строки. `format_human_response` собирает ответ по секциям
#: и выбрасывает всё, чего не узнала по фиксированному префиксу — на этом уже
#: погибал хвост проверки (см. `TAIL_PREFIX`), и на этом же живьём 2026-08-15
#: погибло это предупреждение: в черновик оно попало, до печати не дошло.
NOTICE_PREFIX = "⚠️ Отвечала запасная модель"


@dataclass(frozen=True)
class SubstitutedRoute:
    """Одна роль, которую отработал не тот, кого выбрали."""

    role: str
    intended: str
    answered: str
    refusals: int
    reason: str


def _name(record: Any) -> str:
    return f"{getattr(record, 'provider', '') or '?'}/{getattr(record, 'model', '') or '?'}"


def _short_reason(error: str | None) -> str:
    if not error:
        return ""
    match = _MESSAGE_RE.search(error)
    text = match.group(1) if match else error.split(":", 1)[0]
    text = " ".join(text.split())
    # Точка снимается: причина встаёт в середину фразы, и своя точка дала бы
    # вторую подряд.
    return text[:_MAX_REASON].rstrip().rstrip(".")


def substituted_routes(records: Any, *, run_id: str | None = None) -> tuple[SubstitutedRoute, ...]:
    """Роли, ответ которых написал запасной поставщик.

    `run_id` сужает до текущего хода: леджер переживает ход, и вчерашняя
    подмена не должна помечать сегодняшний ответ.
    """
    rows = [
        r for r in (records or [])
        if run_id is None or getattr(r, "run_id", None) in (None, run_id)
    ]
    out: dict[str, SubstitutedRoute] = {}
    for row in rows:
        if getattr(row, "status", "") != "success":
            continue
        route_reason = str(getattr(row, "route_reason", "") or "")
        if not route_reason.startswith((FAILOVER_PREFIX, HEALTH_PREFIX)):
            continue
        role = str(getattr(row, "role", "") or "?")
        if route_reason.startswith(HEALTH_PREFIX):
            origin, _, health = route_reason.removeprefix(HEALTH_PREFIX).partition(":")
            health = health.split("|", 1)[0].rsplit("->", 1)[0]
            out[role] = SubstitutedRoute(
                role, origin or "основной поставщик", _name(row), 0, health[:_MAX_REASON],
            )
            continue
        refused = [
            r for r in rows
            if getattr(r, "role", None) == role and getattr(r, "status", "") == "error"
        ]
        if not refused:
            # Подмена без единого отказа в этом же ходе означает, что отказ
            # случился раньше и роутер остался на запасном. Сообщать всё равно
            # надо — молчание тут и есть дефект, — но причину назвать нечем.
            out[role] = SubstitutedRoute(role, "основная модель", _name(row), 0, "")
            continue
        out[role] = SubstitutedRoute(
            role=role,
            intended=_name(refused[0]),
            answered=_name(row),
            refusals=len(refused),
            reason=_short_reason(getattr(refused[-1], "error", None)),
        )
    return tuple(out.values())


def substitution_notice(
    routes: tuple[SubstitutedRoute, ...], *, rejected_draft: bool = False,
) -> str:
    """Одна строка для хвоста ответа. Пусто, когда подмены не было."""
    if not routes:
        return ""
    answered = sorted({r.answered for r in routes})
    intended = sorted({r.intended for r in routes})
    refusals = sum(r.refusals for r in routes)
    reason = next((r.reason for r in routes if r.reason), "")
    notice = (
        f"{NOTICE_PREFIX} {', '.join(answered)} — основная "
        f"{', '.join(intended)} недоступна"
    )
    if refusals:
        # «отказов: N», а не «N отказов»: склонение по числу здесь ничего не
        # добавляет, а ошибиться в нём легко.
        notice += f" (отказов: {refusals})"
    if reason:
        notice += f": {reason}"
    if rejected_draft:
        return (
            notice + ". Модель подготовила отклонённый черновик; "
            "уведомление об отказе сформировано проверкой ответа."
        )
    return notice + ". Качество этого ответа — её, а не основной."

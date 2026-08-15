"""Ответ написала не та модель, которую выбрал маршрут.

Роутер умеет подменять недоступного поставщика запасным и делает это молча:
в журнале появляется `route_reason=provider_failover:anthropic->openai`, а в
ОТВЕТЕ — ничего. Живой сеанс 2026-08-15: десять отказов подряд, все пять ходов
написал `gpt-4o-mini`, и оператор пять ходов спорил с подменой о её же
способностях, принимая её отговорки за свойства своего агента.

Подмена — факт о доверии к ответу, такой же природы, как «соответствие вопросу»
в том же хвосте: она не про правдивость отдельного утверждения, а про то, чем
этот ответ вообще написан. Поэтому она сообщается рядом с ними и НЕ зависит от
того, нашлись ли проверяемые утверждения: ход без единой цитаты, написанный
запасной моделью, требует предупреждения ровно так же.

Читается из леджера расходов, а не из нового провода: маршрут там уже записан
по каждому вызову, и заводить второй источник той же правды значило бы дать им
возможность разойтись.

Зачем и чем мерялось: docs/CODE_NOTES.md, «The answer was not written by the
model you chose».
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: Метка, которой роутер помечает вызов, ушедший к запасному поставщику.
FAILOVER_PREFIX = "provider_failover:"

#: Человеческая часть ошибки провайдера. Внутри — repr словаря из SDK, и
#: сообщение живёт только там; если формат сменится, остаётся класс исключения.
_MESSAGE_RE = re.compile(r"'message':\s*'([^']{4,400})'")

#: Хвост ответа читает оператор, а не журнал: длинная простыня в нём вытеснит
#: то, ради чего он написан.
_MAX_REASON = 160


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
        if not str(getattr(row, "route_reason", "") or "").startswith(FAILOVER_PREFIX):
            continue
        role = str(getattr(row, "role", "") or "?")
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


def substitution_notice(routes: tuple[SubstitutedRoute, ...]) -> str:
    """Одна строка для хвоста ответа. Пусто, когда подмены не было."""
    if not routes:
        return ""
    answered = sorted({r.answered for r in routes})
    intended = sorted({r.intended for r in routes})
    refusals = sum(r.refusals for r in routes)
    reason = next((r.reason for r in routes if r.reason), "")
    notice = (
        f"⚠️ Отвечала запасная модель {', '.join(answered)} — основная "
        f"{', '.join(intended)} недоступна"
    )
    if refusals:
        # «отказов: N», а не «N отказов»: склонение по числу здесь ничего не
        # добавляет, а ошибиться в нём легко.
        notice += f" (отказов: {refusals})"
    if reason:
        notice += f": {reason}"
    return notice + ". Качество этого ответа — её, а не основной."

"""Что агент знает о себе ИЗМЕРЕНИЕМ, а не из подсказки."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from contextvars import ContextVar
from typing import Any

#: То, что блок ПОКАЗАЛ модели в этом прогоне, — чтобы верификатор принимал
#: ровно те поля, на которые подсказка разрешает ссылаться `[runtime:<поле>]`.
#: Замер 2026-09-19 (одна задача пять раз подряд): блок печатал run_id,
#: хранилища и durable_writes, а пул улик знал только пять полей процесса;
#: «(run_id=…) [runtime:run_id]» вырезалось как выдуманная ссылка, и верный
#: эпизод терял допуск в опыт за то, что сделал, как велели.
_SHOWN: ContextVar[dict[str, str]] = ContextVar("runtime_self_shown", default={})


def shown_facts(run_id: str) -> dict[str, str]:
    """Поля, показанные блоком в прогоне `run_id`; чужой прогон — пусто."""
    facts = _SHOWN.get()
    return dict(facts) if run_id and facts.get("run_id") == run_id else {}

#: Порядок фиксирован: читателю нужен один и тот же список от хода к ходу,
#: иначе «органа не назвали» и «органа не было» опять сольются.
_ORGAN_ORDER: tuple[str, ...] = (
    "working_memory",
    "persistent_store",
    "episodic_store",
    "procedural_store",
    "source_registry_store",
    "user_profile_store",
    "approval_provider",
)


def process_facts() -> dict[str, str]:
    """Чем агент ИСПОЛНЯЕТСЯ — измеренное, не выведенное."""
    import os
    import platform
    import sys

    return {
        "interpreter": sys.executable or "(unknown)",
        "python_version": platform.python_version(),
        "platform": sys.platform,
        "pid": str(os.getpid()),
        "cwd": os.getcwd(),
    }


def runtime_self_block(
    *,
    trace_id: str,
    run_id: str,
    session_id: str | None,
    stores: Mapping[str, Any],
    durable_writes: Iterable[str] | None,
    tools: Iterable[str] | None = None,
) -> str:
    """Факты о текущем исполнении, пригодные для проверки по журналу.

    Пустая строка, если идентичность прогона неизвестна: выдумывать её — то
    же самое, что выдумывать личность, только мельче.
    """
    if not trace_id or not run_id:
        _SHOWN.set({})
        return ""
    shown = {"run_id": run_id, "trace_id": trace_id, "session_id": session_id or "нет"}

    lines = [
        "<runtime_self>",
        "Проверяемые факты об этом исполнении. Не описание личности:",
        "чем ты являешься, здесь не сказано — сказано, что подключено.",
        f"  run_id={run_id} trace_id={trace_id} session_id={session_id or 'нет'}",
    ]

    # Тело — перед органами: «на чём я исполняюсь» первичнее, чем «что ко мне
    # подключено». Цитируется как [runtime:<поле>].
    for key, value in process_facts().items():
        lines.append(f"  {key}: {value}")

    named = set(stores)
    for organ in (*_ORGAN_ORDER, *sorted(named - set(_ORGAN_ORDER))):
        if organ not in named:
            continue
        state = "подключён" if stores.get(organ) is not None else "нет"
        lines.append(f"  {organ}: {state}")
        shown[organ] = state

    # `None` — присутственная сессия, где разрешены ВСЕ приёмники (контракт
    # app/bootstrap.build_agent). До 2026-09-03 вывеска печатала его как «ничего
    # не разрешено», и агент в разговоре не собирал даже вызов memory_bank:
    # дверь была открыта, вывеска — закрыта.
    if durable_writes is None:
        writes = "все приёмники (присутственная сессия)"
    else:
        allowed = sorted(str(w) for w in durable_writes)
        writes = ", ".join(allowed) if allowed else "ничего не разрешено"
    lines.append(f"  durable_writes: {writes}")
    # Замер 2026-09-19 (рабочий экзамен): на «справишься ли ты отправить письмо»
    # и «берёшься ли разослать 200 писем» агент отвечал «да» — список своих
    # инструментов он видел только в планировщике. Действие без инструмента
    # (почта, звонок, оплата) — «нет», и это теперь измеренный факт о себе.
    if tools is not None:
        shown["tools"] = ", ".join(sorted(tools)) or "нет"
        lines.append(f"  tools: {shown['tools']}")
    lines.append("</runtime_self>")
    _SHOWN.set({**shown, "durable_writes": writes})
    return "\n".join(lines)

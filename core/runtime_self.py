"""Что агент знает о себе ИЗМЕРЕНИЕМ, а не из подсказки.

ЗАЧЕМ. 2026-08-10 оператор спросил, что агент знает о себе. Ответ — «я
исследовательский агент с модульной архитектурой», верификация 10 из 10,
соответствие вопросу 0.00. Причина не в том, что он подсмотрел в `tools/`:
`core/answer_format.py` начинается словами `You are a careful research
analyst.` Личность НАЗНАЧЕНА контрактом вывода, и агент ответил верно
относительно назначенной роли — беспочвенна сама роль.

При этом в синтез приходил `profile_to_prompt_block` — профиль ОПЕРАТОРА.
Состав собственного организма модель не получала вовсе, хотя `app/bootstrap.py`
его знает: какие хранилища подняты, какие долговечные записи разрешены, под
каким прогоном идёт ход.

ГРАНИЦА, и она здесь главная. Этот модуль сообщает ТОЛЬКО проверяемое: то, что
можно сверить с журналом прогона и с составом, собранным при постройке. Он не
объявляет, кем агент является: персона живёт в `SYSTEM_ANSWER` и остаётся
решением оператора. Вторая беспочвенная роль была бы не лучше первой.

ОТСУТСТВУЮЩИЙ ОРГАН НАЗЫВАЕТСЯ. Промолчать о неподключённом хранилище значит
сделать пропуск неотличимым от наличия — ровно тот порок, который эта система
чинит весь день. Поэтому в списке стоят и подключённые, и нет.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

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
    """Чем агент ИСПОЛНЯЕТСЯ — измеренное, не выведенное.

    Заведено 2026-08-14. До этого модуль знал семь хранилищ и не знал, что он
    процесс Python. Замер: на вопрос «ты видишь Python?» агент выполнил
    `where python`, обыскал диск и нашёл интерпретатор — **работая на нём**, —
    и честно приписал, что работоспособность не проверял. Доказательство
    работоспособности — он сам; предъявить его было нечем, потому что
    собственное исполнение не проходило ни через один инструмент.

    Здесь только то, что интерпретатор сообщает о себе. Ни версии пакетов, ни
    состава окружения: чем длиннее список, тем выше шанс, что часть его
    протухнет молча, а этот модуль существует ради обратного.
    """
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
    durable_writes: Iterable[str],
) -> str:
    """Факты о текущем исполнении, пригодные для проверки по журналу.

    Пустая строка, если идентичность прогона неизвестна: выдумывать её — то
    же самое, что выдумывать личность, только мельче.
    """
    if not trace_id or not run_id:
        return ""

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

    allowed = sorted(str(w) for w in durable_writes)
    lines.append(
        f"  durable_writes: {', '.join(allowed) if allowed else 'ничего не разрешено'}"
    )
    lines.append("</runtime_self>")
    return "\n".join(lines)

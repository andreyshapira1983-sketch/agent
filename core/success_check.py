"""Критерий успеха: та его часть, которую можно наблюдать без модели.

Зачем существует. Хартия требует от каждой цели `success_check` («цель без
проверки — желание», core/charter_goal.py), но до аудита автономности
2026-09-17 этот критерий нигде не применялся: `_pick_next_goal` отдавал наружу
одну строку цели, а `_task_goal` объявлял задачу выполненной по единственному
признаку — ответ модели не пуст. «Я создал файл» и созданный файл были для
системы одним событием.

Здесь НЕ появляется универсальный проверяющий. Критерий — свободный текст, и
понять его целиком нельзя, не спросив ту же модель, чьё слово мы и проверяем.
Поэтому берётся ровно наблюдаемая часть: НАЗВАННЫЕ в критерии пути. Их
существование читается с диска — независимо от того, что сказал исполнитель.

Три исхода, и третий назван словом намеренно:

* ``verified``      — все названные следы найдены в мире;
* ``missing``       — хотя бы один назван и не найден;
* ``unverifiable``  — критерий не называет наблюдаемого следа.

``unverifiable`` — не «да». Запись исполнения обязана отличать «сошлось» от
«не проверяли»: незнание, выданное за проверку, хуже отсутствия проверки.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

#: Путеподобный токен: основа со ЛАТИНСКИМИ буквами и расширение из букв.
#: Кириллица исключена намеренно — «цель.Проверка» не путь, а конец фразы.
_PATH_TOKEN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./\\-]*\.[A-Za-z][A-Za-z0-9]{0,7}")

#: Обрамление, которое человек и модель ставят вокруг имени файла.
_TRIM = " \t\"'`«»(),;:!?[]{}<>"

#: Расширения, которые чаще встречаются как конец предложения или версия, чем
#: как файл. Пустой по умолчанию: список-исключение — место, где проверка
#: тихо перестаёт работать, поэтому он заводится только по замеру.
_IGNORED_SUFFIXES: frozenset[str] = frozenset()


def named_artifacts(success_check: str) -> tuple[str, ...]:
    """Пути, НАЗВАННЫЕ в критерии успеха, в порядке появления, без повторов.

    Токен считается путём, когда у него есть основа и буквенное расширение
    (``result.json``, ``docs/notes/plan.md``). Версии и числа (``1.5``) не
    проходят: расширение обязано начинаться с буквы.
    """
    seen: list[str] = []
    for match in _PATH_TOKEN.finditer(str(success_check or "")):
        token = match.group(0).strip(_TRIM).replace("\\", "/")
        if not token or token.endswith("/"):
            continue
        if token.rsplit(".", 1)[-1].lower() in _IGNORED_SUFFIXES:
            continue
        if token not in seen:
            seen.append(token)
    return tuple(seen)


def observe_success_check(success_check: str, workspace: Any) -> dict:
    """Прочитать МИР и сказать, сошёлся ли критерий. Модель не спрашивается.

    Возвращает словарь с полями ``verdict``, ``artifacts``, ``missing``,
    ``observed`` — он же ложится в запись исполнения, чтобы «сошлось» можно
    было перепроверить после прогона, не повторяя его.
    """
    artifacts = named_artifacts(success_check)
    if not artifacts:
        return {
            "verdict": "unverifiable",
            "artifacts": [],
            "missing": [],
            "observed": {},
            "reason": "критерий не называет наблюдаемого следа",
        }
    root = Path(workspace or ".")
    observed: dict[str, dict] = {}
    missing: list[str] = []
    for relpath in artifacts:
        target = root / relpath
        try:
            exists = target.exists()
            size = target.stat().st_size if target.is_file() else None
        except OSError:
            exists, size = False, None
        observed[relpath] = {"exists": bool(exists), "size": size}
        if not exists:
            missing.append(relpath)
    return {
        "verdict": "missing" if missing else "verified",
        "artifacts": list(artifacts),
        "missing": missing,
        "observed": observed,
        "reason": (
            f"не найдено в мире: {', '.join(missing)}" if missing
            else f"наблюдалось в мире: {', '.join(artifacts)}"
        ),
    }

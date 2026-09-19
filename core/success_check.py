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

#: Утверждения о СОДЕРЖИМОМ, которые можно проверить без модели. Ровно две
#: формы, и обе однозначны:
#:   * `status=ready` — пара через знак равенства, без пробелов;
#:   * `"status": "ready"` — кусок JSON, приведённый в критерии дословно.
#: Двоеточие без кавычек НЕ берётся намеренно: «признак успеха: файл готов» —
#: обычная фраза, и требовать её содержимого от файла значит выдумать критерий,
#: которого никто не ставил. Ревизия PR #333 назвала дыру, а не попросила
#: угадывать смысл свободного текста.
_KV_PLAIN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{0,63})=([A-Za-z0-9_.-]{1,64})")
_KV_JSON = re.compile(
    r"[\"']([A-Za-z_][A-Za-z0-9_]{0,63})[\"']\s*:\s*[\"']([^\"']{1,64})[\"']"
)

#: Сколько байт файла читается ради проверки литерала. Критерий говорит о
#: небольшом следе; читать гигабайт ради слова — способ уронить тик.
_CONTENT_READ_LIMIT = 256 * 1024


def content_claims(success_check: str) -> tuple[tuple[str, str], ...]:
    """Пары «ключ, значение», названные критерием, без повторов."""
    seen: list[tuple[str, str]] = []
    text = str(success_check or "")
    for pattern in (_KV_JSON, _KV_PLAIN):
        for match in pattern.finditer(text):
            pair = (match.group(1), match.group(2))
            if pair not in seen:
                seen.append(pair)
    return tuple(seen)


def _claim_pattern(key: str, value: str) -> re.Pattern[str]:
    """Пара, найденная в файле в любой из обычных записей.

    `status=ready` обязано узнавать `{"status": "ready"}`, `status: ready` и
    `status=ready`: критерий говорит об утверждении, а не о синтаксисе. Между
    ключом и значением допускаются только разделители — так `status` и `ready`,
    разбросанные по разным строкам, парой не считаются.

    Границы по краям — ревизия PR #334. Без них `status=ready` засчитывался на
    `{"status": "ready_to_fail"}` и на `{"my_status": "ready"}`. Первое хуже
    второго: это ложное ПОДТВЕРЖДЕНИЕ, то есть критерий «готово» закрывался
    файлом, буквально говорящим «готово провалиться».
    """
    return re.compile(
        rf"(?<![\w.-])[\"']?{re.escape(key)}[\"']?\s*[:=]\s*"
        rf"[\"']?{re.escape(value)}(?![\w-])[\"']?",
        re.IGNORECASE,
    )


def _claims_met(target: Path, claims: tuple[tuple[str, str], ...]) -> tuple[bool, str]:
    """Сошлись ли утверждения о содержимом. Нечитаемый файл — это «нет».

    Fail-closed по смыслу ворот: «не смог прочитать» не вправе означать
    «сошлось». Иначе двоичный или заблокированный файл снова превращает
    вердикт в пересказ чужого слова.
    """
    if not claims:
        return True, ""
    try:
        with target.open("r", encoding="utf-8", errors="replace") as fh:
            text = fh.read(_CONTENT_READ_LIMIT)
    except OSError as exc:
        return False, f"содержимое не прочитано ({type(exc).__name__})"
    unmet = [
        f"{key}={value}" for key, value in claims
        if not _claim_pattern(key, value).search(text)
    ]
    if unmet:
        return False, "не найдено в содержимом: " + ", ".join(unmet)
    return True, ""


def _artifact_tokens(text: str) -> list[tuple[int, str]]:
    """Путеподобные токены вместе с местом, где они названы.

    Место нужно привязке утверждений: «result.json содержит status=ready»
    ставит утверждение ПОСЛЕ своего следа, и это единственный признак
    принадлежности, который есть в свободном тексте.
    """
    found: list[tuple[int, str]] = []
    for match in _PATH_TOKEN.finditer(str(text or "")):
        token = match.group(0).strip(_TRIM).replace("\\", "/")
        if not token or token.endswith("/"):
            continue
        if token.rsplit(".", 1)[-1].lower() in _IGNORED_SUFFIXES:
            continue
        found.append((match.start(), token))
    return found


def named_artifacts(success_check: str) -> tuple[str, ...]:
    """Пути, НАЗВАННЫЕ в критерии успеха, в порядке появления, без повторов.

    Токен считается путём, когда у него есть основа и буквенное расширение
    (``result.json``, ``docs/notes/plan.md``). Версии и числа (``1.5``) не
    проходят: расширение обязано начинаться с буквы.
    """
    seen: list[str] = []
    for _pos, token in _artifact_tokens(success_check):
        if token not in seen:
            seen.append(token)
    return tuple(seen)


def claims_by_artifact(success_check: str) -> dict[str, tuple[tuple[str, str], ...]]:
    """Какие утверждения о содержимом к какому следу относятся.

    Ревизия PR #334: утверждения были общими и проверялись у КАЖДОГО
    названного следа. Критерий «result.json содержит status=ready, и создан
    log.txt» требовал `status=ready` ещё и от `log.txt` — ложный отказ, после
    которого агент принимался чинить работающее.

    Признак принадлежности один: утверждение относится к последнему следу,
    названному ПЕРЕД ним. Утверждение, стоящее раньше всех следов («в
    result.json должно быть status=ready» с обратным порядком слов не бывает,
    но «status=ready должен появиться в result.json» бывает), достаётся
    первому следу. Для критерия с одним следом поведение прежнее.
    """
    text = str(success_check or "")
    tokens = _artifact_tokens(text)
    if not tokens:
        return {}
    bound: dict[str, list[tuple[str, str]]] = {}
    for pattern in (_KV_JSON, _KV_PLAIN):
        for match in pattern.finditer(text):
            owner = tokens[0][1]
            for pos, token in tokens:
                if pos < match.start():
                    owner = token
            pair = (match.group(1), match.group(2))
            slot = bound.setdefault(owner, [])
            if pair not in slot:
                slot.append(pair)
    return {name: tuple(pairs) for name, pairs in bound.items()}


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
            "claims": [],
            "missing": [],
            "observed": {},
            "reason": "критерий не называет наблюдаемого следа",
        }
    root = Path(workspace or ".")
    try:
        root_resolved = root.resolve()
    except OSError:
        root_resolved = root
    claims = content_claims(success_check)
    owned = claims_by_artifact(success_check)
    observed: dict[str, dict] = {}
    missing: list[str] = []
    reasons: list[str] = []
    for relpath in artifacts:
        target = root / relpath
        # Сдерживание раньше наблюдения: критерий не вправе РАСШИРЯТЬ область,
        # в которую смотрит проверяющий. След, положенный снаружи копии, не
        # засчитывает цель внутри неё (ревизия PR #333).
        try:
            resolved = target.resolve()
            escaped = root_resolved != resolved and root_resolved not in resolved.parents
        except OSError:
            resolved, escaped = target, True
        if escaped:
            observed[relpath] = {"exists": False, "size": None, "outside": True}
            missing.append(relpath)
            reasons.append(f"{relpath}: ведёт за пределы рабочей копии")
            continue
        try:
            exists = target.exists()
            size = target.stat().st_size if target.is_file() else None
        except OSError:
            exists, size = False, None
        entry: dict[str, Any] = {"exists": bool(exists), "size": size}
        if not exists:
            observed[relpath] = entry
            missing.append(relpath)
            continue
        met, why = _claims_met(target, owned.get(relpath, ()))
        entry["claims_met"] = met
        observed[relpath] = entry
        if not met:
            missing.append(relpath)
            reasons.append(f"{relpath}: {why}")
    return {
        "verdict": "missing" if missing else "verified",
        "artifacts": list(artifacts),
        "claims": [f"{k}={v}" for k, v in claims],
        "missing": missing,
        "observed": observed,
        "reason": (
            ("; ".join(reasons) if reasons else f"не найдено в мире: {', '.join(missing)}")
            if missing
            else f"наблюдалось в мире: {', '.join(artifacts)}"
        ),
    }

"""Болтовня без дела идёт коротким путём: без планировщика, без инструментов, без отчёта.

Слово оператора 24.09: «короткий путь для болтовни, чтобы шутка не запускала весь
рабочий конвейер». Замер того же дня: на лёгкую реплику агент устраивал пять
кругов поиска по журналам, собирал 55 улик и отвечал списком с пометками и
строкой «Проверка: подтверждено 4 из 7».

Сегодняшний короткий путь (`task_complexity.can_skip_planner`) узнаёт только
«чистое приветствие» по словарю, и словарь уже раз расширяли (MIR-020). Записка
органа разговора (docs/audit/CONVERSATION_ORGAN_DESIGN.md, урок AIML/ALICE)
запрещает растить его дальше: когда одной регулярки мало, орган сменяется
КЛАССИФИКАТОРОМ, а не толстеет. Практика маршрутизаторов агентов та же: слой
намерения («chit_chat» против задачи) стоит перед выбором инструментов.

Цена ошибки несимметрична. Болтовня, принятая за дело, стоит лишь лишнего
вызова планировщика. Дело, принятое за болтовню, теряет инструменты. Поэтому:
  * сначала те же дисквалификаторы, что у словарного пути (подсказка файла,
    слово-признак инструмента, живые данные, глубокая задача, длина) — без
    вызова модели;
  * модель отвечает «social» только когда для ответа не нужен ни один факт — ни
    о мире, ни о файлах, ни о новостях, ни о делах и журналах самого агента;
  * сомнение, сбой, неразборчивый ответ — обычный путь.
"""
from __future__ import annotations

import json
import re
from typing import Any

from core.task_complexity import (
    ComplexityTier,
    assess_complexity,
    needs_live_grounding,
    tool_signal_present,
)

#: Длиннее — уже не реплика, а поручение; классификатор не зовётся.
MAX_CHARS = 300

_SYSTEM = (
    "Ты классифицируешь реплику человека автономному агенту. Ответь только JSON "
    "{\"social\": true} или {\"social\": false}. social=true ТОЛЬКО если это чистый "
    "разговор — приветствие, шутка или просьба пошутить, благодарность, вопрос о "
    "настроении, комплимент, прощание — и для ответа НЕ нужен ни один факт: ни о "
    "мире, ни о файлах, ни о новостях, ни о числах, ни о делах, журналах и промахах "
    "самого агента (что он делал, когда, сколько). Если нужен хоть один такой факт "
    "или есть сомнение — social=false."
)
_JSON_RE = re.compile(r"\{[^{}]*\}")


def is_social_turn(loop: Any, text: str, *, file_hint: str | None = None) -> bool:
    """Реплика — чистая болтовня, которой не нужны ни планировщик, ни инструменты."""
    if file_hint or not isinstance(text, str):
        return False
    stripped = text.strip()
    if not stripped or len(stripped) > MAX_CHARS:
        return False
    if (tool_signal_present(stripped.casefold()) or needs_live_grounding(stripped)
            or assess_complexity(stripped) is ComplexityTier.DEEP):
        return False
    router = getattr(loop, "model_router", None)
    if router is None:
        return False
    try:
        from core.model_router import ModelRole

        raw = router.for_role(ModelRole.MEMORY_SUMMARY).complete(
            system=_SYSTEM, user=stripped, max_tokens=20, temperature=0.0, json_object=True)
        found = _JSON_RE.search(str(raw or ""))
        social = bool(found) and json.loads(found.group(0)).get("social") is True
    except Exception:  # noqa: BLE001 — сбой классификатора = обычный путь, никогда не короткий
        social = False
    log = getattr(loop, "log", None)
    if log is not None:
        log.log("social_turn_classified", {"social": social, "chars": len(stripped)})
    if social:
        # Та же реплика не несёт хвоста «Проверка: …» (loop_response_deciders).
        loop._social_question = stripped
    return social


def is_marked_social(loop: Any, question: str) -> bool:
    """Классификатор уже признал эту реплику болтовнёй в этом ходе."""
    return bool(question) and question.strip() == getattr(loop, "_social_question", None)

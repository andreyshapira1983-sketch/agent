"""Short path for idle chatter: no planner, no tools, no report.

The dictionary path (`task_complexity.can_skip_planner`) recognises only a pure greeting; here a model CLASSIFIER decides.
Error cost is asymmetric (chatter taken for work costs one planner call; work taken for chatter loses the tools), so:
the same disqualifiers as the dictionary path run first without a model call; the model answers `social` only when no fact
is needed; doubt, failure or an unparseable reply takes the normal path; an exhausted budget is not a classifier failure
and propagates.

Only in conversation with a human (AGENT_SOCIAL_TURN_CLASSIFIER, set by the chat bridge): campaign goals are never chatter.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from core.task_complexity import (
    ComplexityTier,
    assess_complexity,
    needs_live_grounding,
    tool_signal_present,
)

#: Переключатель: включает мостик разговора (ask_agent.py), не кампания.
ENV_FLAG = "AGENT_SOCIAL_TURN_CLASSIFIER"

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
    """The utterance is pure chatter that needs neither planner nor tools."""
    if os.environ.get(ENV_FLAG, "").strip().lower() not in ("1", "on", "true", "yes"):
        return False
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
    from core.model_router import ModelRole
    from core.model_usage import ModelBudgetExceeded

    try:
        raw = router.for_role(ModelRole.MEMORY_SUMMARY).complete(
            system=_SYSTEM, user=stripped, max_tokens=20, temperature=0.0, json_object=True)
        found = _JSON_RE.search(str(raw or ""))
        social = bool(found) and json.loads(found.group(0)).get("social") is True
    except ModelBudgetExceeded:
        raise
    except Exception:  # noqa: BLE001 — сбой классификатора = обычный путь, никогда не короткий
        social = False
    log = getattr(loop, "log", None)
    if log is not None:
        log.log("social_turn_classified", {"social": social, "chars": len(stripped)})
    if social:
        # The same utterance carries no "Check: ..." tail (loop_response_deciders).
        loop._social_question = stripped
    return social


def is_marked_social(loop: Any, question: str) -> bool:
    """The classifier already judged this utterance chatter in this turn."""
    return bool(question) and question.strip() == getattr(loop, "_social_question", None)

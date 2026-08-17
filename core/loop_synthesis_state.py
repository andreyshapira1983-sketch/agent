"""State holder for `core.loop_synthesis`.

Split out to keep `core/loop_synthesis.py` small while preserving its public API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from core.models import Goal
from core.planner import PlannerOutput


@dataclass
class SynthesisState:
    """То, что вызов синтезатора носит с собой за один прогон.

    Имена полей совпадают с прежними локальными именами `_run_inner` — это
    условие проверяемости переноса: подстановка `имя -> st.имя` механическая,
    и тест сверяет её с историей.
    """

    # ── Вход ─────────────────────────────────────────────────────────────
    goal: Goal
    user_question: str
    file_hint: str | None
    artifacts: dict[str, dict[str, Any]]
    planner_out: PlannerOutput
    plan: Any
    history: str
    persistent_block: str
    failure_history: list[Any]
    replan_exhausted: bool
    cheap_path_active: bool
    local_critique_active: bool
    _task_synth_llm: Any
    _cp: Any

    # ── Выход ────────────────────────────────────────────────────────────
    draft_answer: str = ""
    #: Изменяемая ячейка, а не поле-строка: её пишет ЗАМЫКАНИЕ внутри
    #: лестницы, и на каждой попытке заново. Прогонная, не на экземпляре:
    #: `self._last_*` пережил бы прогон, а ранние выходы (реплей, отказ)
    #: банкуют, сюда не заходя, — вердикт одного хода приписался бы эпизоду
    #: следующего.
    _declared: dict[str, str | None] | None = None

    OUTPUTS: ClassVar[frozenset[str]] = frozenset({"draft_answer", "_declared"})

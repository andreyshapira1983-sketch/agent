"""Наблюдение, разбор запроса и выбор модели — вырезано из ``core/loop.py``.

Правило оператора: «ни один файл кода не длиннее 2000 строк» и «разбирай
большие файлы на компактные подключаемые модули — не дублируя и не искажая».
Шестой кусок раскола `core/loop.py` и четвёртый — раскола `_run_inner`.

Начало цикла, фазы §3 «Observe» и «Interpret»: наблюдение, классификация
САМОГО вопроса, маршрут роли, адаптивный выбор модели и цель.

Классификация стоит здесь, до всего остального, по одной причине: секрет,
вставленный в промпт, ядро обязано поймать ДО того, как его увидит модель.
Переносить эту проверку ниже по коду нельзя — она перестанет быть тем, чем
является.

`TypeError` из маршрутизатора моделей ПРОЛЕТАЕТ наружу намеренно: это дефект
сигнатуры вызова, а не сбой маршрутизации. Пока его глушили общим откатом на
модель по умолчанию, каждая задача тихо отвечала не на той модели, и в
журнале не было ни строчки о том, что маршрутизация перестала работать.

Тело перенесено символ в символ, что пинится AST-сверкой с историей в
`tests/test_loop_observe_split.py`.

Класс подмешивается в ``AgentLoop``; состояние по-прежнему живёт на
композированном цикле, а не здесь.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.data_classifier import DataClass, classify
from core.model_router import ModelRole
from core.models import Goal, Observation
from core.redaction import collect_pii_findings, scan


class AgentLoopObserve:
    """Фазы «Observe» и «Interpret»: что спросили и чем это отвечать.

    Члены ниже — объявления контракта хоста (``AgentLoop`` их создаёт в
    ``__init__``); присваиваний нет, поэтому во время выполнения ничего не
    создаётся и не затеняется. Тот же приём, что в ``loop_step_execution``.
    """

    if TYPE_CHECKING:  # pragma: no cover — только объявления
        log: Any
        role_router: Any
        model_router: Any
        last_role_context: Any
        # Как в `AgentLoop.__init__`: список или None до начала цикла.
        _cycle_findings: list[dict[str, Any]] | None

        # Объявляем ВЫЗЫВАЕМЫМ атрибутом: заглушка-функция с пустым телом
        # читается анализаторами как «функция без return», и каждый вызов
        # ложно помечается E1111.

    def _observe_and_route(
        self,
        user_question: str,
        *,
        file_hint: str | None,
        deep_escalation: bool,
        _cp: Any,
    ) -> tuple[Goal, Any, Any]:
        """Цель хода и две модели под него (планировщик, синтезатор).

        Модели могут быть `None` — маршрутизатор упал не по вине вызова, и
        вызывающий тогда работает на модели по умолчанию. Имя `_cp`
        (писатель контрольных точек) сохранено как в цикле: перенос дословный.
        """
        observation = Observation(
            source="cli",
            modality="text",
            content={
                "question": user_question,
                "file_hint": file_hint,
            },
            provenance="user",
        )
        self.log.log("observe", observation)
        _cp.save_observe(question=user_question, file_hint=file_hint)

        # Classify the question itself. A user can paste a secret into the
        # prompt; the kernel must catch it BEFORE the LLM sees it.
        q_cls = classify(user_question, source="cli")
        self.log.log(
            "data_classified",
            {
                "label": "user_question",
                "class": q_cls.cls.value,
                "source": q_cls.source,
                "reasons": q_cls.reasons,
            },
        )
        if q_cls.cls == DataClass.SECRET:
            q_findings = scan(user_question)
            kinds = sorted({f.kind for f in q_findings})
            self.log.log(
                "secret_detected",
                {
                    "label": "user_question",
                    "kinds": kinds,
                    "count": len(q_findings),
                    "surface": "user_input",
                },
            )
            self._cycle_findings.append(
                {"label": "user_question", "kinds": kinds, "count": len(q_findings)}
            )
        elif q_cls.cls == DataClass.SENSITIVE:
            q_findings = collect_pii_findings(user_question)
            kinds = sorted({f"pii-{f.kind}" for f in q_findings})
            self.log.log(
                "sensitive_detected",
                {
                    "label": "user_question",
                    "kinds": kinds,
                    "count": len(q_findings),
                    "surface": "user_input",
                },
            )
            self._cycle_findings.append(
                {"label": "user_question", "kinds": kinds, "count": len(q_findings)}
            )

        self.last_role_context = self.role_router.route(user_question)
        self.log.log("role_route", self.last_role_context.to_log_payload())

        # Adaptive model routing: choose LLM tier based on task complexity.
        # LIGHT tasks (greetings, quick lookups) get a cheap/fast model.
        # DEEP tasks (architecture, full audits, from-scratch builds) get
        # the most powerful available model (opus, o1, …).
        # STANDARD tasks reuse the default role route — no change.
        # The RoleRouter verdict computed just above is forwarded so a repair
        # or programming task can never be downgraded to the LIGHT tier on the
        # strength of a terse phrasing alone.
        #
        # Failure handling mirrors `_record_experience_memory`: a `TypeError`
        # here is a CALL-SIGNATURE DEFECT, not a routing fault -- `for_task()`
        # was invoked with an argument it does not accept (or without one it
        # requires). Laundering that into a silent fallback hid the defect
        # completely: every task would quietly answer on the default model with
        # nothing in the log to say routing had stopped working. So `TypeError`
        # PROPAGATES; every other failure keeps the default-LLM fallback but is
        # now recorded instead of vanishing.
        try:
            _task_role = getattr(self.last_role_context, "role", None)
            _task_planner_llm = self.model_router.for_task(
                ModelRole.PLANNER,
                user_question,
                escalation=deep_escalation,
                task_role=_task_role,
            )
            _task_synth_llm = self.model_router.for_task(
                ModelRole.SYNTHESIZER,
                user_question,
                escalation=deep_escalation,
                task_role=_task_role,
            )
            _planner_model = getattr(_task_planner_llm, "model", None)
            _synth_model = getattr(_task_synth_llm, "model", None)
            _route_reason = getattr(
                getattr(_task_planner_llm, "route", None), "reason", "default"
            )
            self.log.log(
                "adaptive_route",
                {
                    "question_chars": len(user_question),
                    "task_role": _task_role,
                    "planner_model": _planner_model,
                    "synth_model": _synth_model,
                    "route_reason": _route_reason,
                },
            )
        except TypeError:
            # Visible on purpose -- see the comment above.
            raise
        except Exception as exc:  # noqa: BLE001
            _task_planner_llm = None
            _task_synth_llm = None
            self.log.log(
                "adaptive_route_error",
                {
                    "error": type(exc).__name__,
                    "detail": str(exc)[:200],
                    "question_chars": len(user_question),
                    "fallback": "default_llm",
                },
            )

        # 2. Interpret -> Goal
        goal = self._interpret(observation)
        self.log.log("interpret", goal)

        return goal, _task_planner_llm, _task_synth_llm

    def _interpret(self, observation: Observation) -> Goal:
        question = observation.content["question"]
        return Goal(
            description=f"Answer the question: {question}",
            success_criteria="A grounded answer citing every claim back to a provided source.",
        )

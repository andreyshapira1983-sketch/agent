"""Контекст хода до планирования — вырезано из ``core/loop.py`` дословно.

Правило оператора: «ни один файл кода не длиннее 2000 строк» и «разбирай
большие файлы на компактные подключаемые модули — не дублируя и не искажая».
Девятый кусок раскола `core/loop.py` и седьмой — раскола `_run_inner`.

Всё, что цикл читает ПЕРЕД тем, как что-то планировать: история разговора,
классификация хода как самоанализа, разрешение референта и выборка из долгой
и опытной памяти. Чтение и только чтение — ни один из этих шагов ничего не
меняет за пределами журнала и полей на цикле.

Классификация самоанализа стоит здесь, до планирования, не для красоты: она
решает, к какому классу улик будет отнесён ответ. Утверждение о ТЕКУЩЕМ
обмене подтверждается стенограммой, а не внешним источником, и решить это
после ответа — значит судить его не по той шкале.

Локальная критика подавляет выдачу долгой и опытной памяти по умолчанию
(PR2): предмет разбора назван явно, и подмешивать к нему воспоминания
означает разбирать не то, о чём спросили.

Тело перенесено символ в символ, что пинится AST-сверкой с историей в
`tests/test_loop_context_split.py`.

Класс подмешивается в ``AgentLoop``; состояние по-прежнему живёт на
композированном цикле, а не здесь.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.assumption_registry import (
    AssumptionRegistry,
    extract_from_question,
)
from core.evidence_classes import is_self_analysis_turn
from core.ids import new_id
from core.referent_resolver import (
    FileHintRef,
    PriorTurnRef,
    ReferentResolver,
    artifacts_from_working_memory,
    citation_token_for_referent,
    is_local_critique_eligible,
    is_show_only_directive,
    referent_resolver_mode,
)


class AgentLoopContext:
    """Чтение контекста хода: история, самоанализ, референт, память.

    Члены ниже — объявления контракта хоста (``AgentLoop`` их создаёт в
    ``__init__``); присваиваний нет, поэтому во время выполнения ничего не
    создаётся и не затеняется. Тот же приём, что в ``loop_step_execution``.
    """

    if TYPE_CHECKING:  # pragma: no cover — только объявления
        log: Any
        memory: Any
        user_profile_store: Any
        last_user_profile: Any
        last_self_analysis: Any
        last_referent_decision: Any
        _last_best_similar_episode: Any
        _last_best_similar_score: Any

        # Объявляем ВЫЗЫВАЕМЫМИ атрибутами: заглушка-функция с пустым телом
        # читается анализаторами как «функция без return», и каждый вызов
        # ложно помечается E1111.
        _retrieve_persistent: Any
        _retrieve_experience_memory: Any

        # Берётся у соседней примеси: работает через MRO, но связь между
        # модулями обязана быть записана, иначе её видно только на прогоне.
        _file_read_workspace_root: Any

    def _retrieve_turn_context(
        self,
        user_question: str,
        *,
        file_hint: str | None,
    ) -> tuple[str, bool, str, str]:
        """История, признак локальной критики, блоки долгой и опытной памяти.

        Порядок здесь — часть контракта: референт разрешается ДО выборки
        памяти, потому что именно его вердикт решает, подмешивать её вообще
        или нет.
        """
        history = ""
        if self.memory is not None:
            history = self.memory.conversation_context(max_turns=5)
            if history:
                self.log.log(
                    "memory_inject",
                    {
                        "session_id": self.memory.session_id,
                        "turns_visible": len(self.memory.recent_turns(5)),
                        "history_chars": len(history),
                        "artifacts_cached": len(self.memory.artifacts),
                    },
                )

        # Issue #119 — conversational correction / self-analysis classification.
        # Decided here, before planning, because it changes which evidence class
        # the answer is later judged against: a claim about THIS session's own
        # exchange is backed by the transcript, not by an external source.
        # Deterministic and always on (no feature flag): its only effect is to
        # admit dialogue evidence that the verifier then scopes narrowly, and a
        # bug the operator cannot report is worse than the risk of that.
        _self_analysis = is_self_analysis_turn(
            user_question,
            has_prior_turn=bool(
                self.memory is not None and self.memory.recent_turns(1)
            ),
        )
        self.last_self_analysis = _self_analysis
        if _self_analysis.is_self_analysis:
            self.log.log("self_analysis_turn", _self_analysis.to_log_payload())

        # Referent resolver (critique PR1/PR2) — shadow logs; on enables path.
        self._maybe_resolve_referent(user_question, file_hint=file_hint)
        local_critique_active = (
            referent_resolver_mode() == "on"
            and self.last_referent_decision is not None
            and is_local_critique_eligible(self.last_referent_decision)
        )
        if local_critique_active:
            _rd = self.last_referent_decision
            assert _rd is not None and _rd.primary is not None
            self.log.log(
                "local_critique_path",
                {
                    "status": _rd.status,
                    "kind": _rd.primary.kind,
                    "show_only": is_show_only_directive(_rd.directive_excerpt),
                    "target_chars": len(_rd.analysis_target_excerpt),
                    "citation": citation_token_for_referent(_rd),
                },
            )

        # Persistent memory retrieval — pick a few long-term records that
        # share keywords with the question, then format them as a
        # <long_term_memory> block injected into planner + synthesizer.
        # Local-critique turns suppress default LTM/episodic injection (PR2).
        if local_critique_active:
            persistent_block = ""
            experience_block = ""
            self._last_best_similar_episode = None
            self._last_best_similar_score = 0.0
        else:
            persistent_block = self._retrieve_persistent(user_question)
            experience_block = self._retrieve_experience_memory(user_question)

        return history, local_critique_active, persistent_block, experience_block

    def _maybe_resolve_referent(
        self,
        user_question: str,
        *,
        file_hint: str | None,
    ) -> None:
        """Shadow/on referent resolution. Shadow logs only; ``on`` enables PR2 path."""
        mode = referent_resolver_mode()
        if mode == "off":
            self.last_referent_decision = None
            return
        try:
            run_id = str(getattr(self.log, "trace_id", "") or new_id("run"))
            session_id = (
                self.memory.session_id if self.memory is not None else run_id
            )
            prior_turns: list[PriorTurnRef] = []
            artifacts = []
            if self.memory is not None:
                prior_turns = [
                    PriorTurnRef(
                        turn_id=turn.id,
                        session_id=session_id,
                        question=turn.question,
                        answer=turn.answer,
                        timestamp=turn.timestamp,
                    )
                    for turn in self.memory.recent_turns(5)
                ]
                artifacts = artifacts_from_working_memory(
                    self.memory.artifacts,
                    session_id=session_id,
                )
            hint_ref: FileHintRef | None = None
            if file_hint and str(file_hint).strip():
                hint_ref = FileHintRef(
                    path=str(file_hint).strip(),
                    turn_id=run_id,
                    session_id=session_id,
                )
            resolver = ReferentResolver(
                workspace_root=self._file_read_workspace_root(),
            )
            decision = resolver.resolve(
                user_question,
                current_session_id=session_id,
                current_turn_id=run_id,
                file_hint=hint_ref,
                artifacts=artifacts,
                prior_turns=tuple(prior_turns),
            )
            self.last_referent_decision = decision
            eligible = is_local_critique_eligible(decision)
            payload = decision.to_dict()
            payload["mode"] = mode
            payload["local_critique_eligible"] = eligible
            # True when enabling ``on`` would change the answer path (PR2).
            payload["would_change_answer"] = eligible
            self.log.log("referent_decision", payload)
        except Exception:
            # Observability must never abort the run.
            self.last_referent_decision = None


    def _open_run(self, user_question: str) -> tuple[Any, Any]:
        """Профиль, реестр допущений и писатель контрольных точек.

        Открывающая часть прогона: всё, что заводится ОДИН раз и до первой
        фазы. Возвращает реестр допущений и писателя — остальное ложится на
        поля цикла.

        Реестр создаётся ЧИСТЫМ: хранилище допущений — архив, а не вход.
        Межходовое авто-восстановление, стоявшее здесь, перетаскивало
        допущения между несвязанными целями (идентификатор трассы живёт всю
        сессию) и не обслуживало ни одного другого потребителя; архивные
        строки возвращаются только явной выборкой (MIR-027).
        """
        # Layer 4 — load the user profile for this cycle.
        if self.user_profile_store is not None:
            self.last_user_profile = self.user_profile_store.load_or_default()
            self.log.log(
                "user_profile_load",
                {
                    "expertise": self.last_user_profile.expertise,
                    "verbosity": self.last_user_profile.verbosity,
                    "language": self.last_user_profile.language,
                    "interaction_count": self.last_user_profile.interaction_count,
                    "interests": self.last_user_profile.interests,
                },
            )

        # Layer 5 — create a fresh AssumptionRegistry and seed it from the question.
        # Layer 4→5 bridge: pass the profile's known language so extract_from_question
        # uses a higher-confidence profile signal instead of a raw heuristic.
        _run_assumptions = AssumptionRegistry(
            run_id=getattr(self.log, "trace_id", ""),
        )
        # MIR-027: the store is an ARCHIVE, not an active input. The cross-
        # turn auto-restore that sat here leaked assumptions between unrelated
        # goals (session-lifetime trace id) and served no other caller;
        # archived rows return only via explicit retrieval. Measurements and
        # the operator's ruling live in the MIR-027 registry entry.
        try:
            _known_lang: str | None = None
            if self.last_user_profile is not None:
                _known_lang = self.last_user_profile.language or None
            _q_assumptions = extract_from_question(
                user_question,
                run_id=getattr(self.log, "trace_id", ""),
                known_language=_known_lang,
            )
            _run_assumptions.register_many(_q_assumptions)
        except Exception:
            pass  # Assumption extraction must never abort the run.

        # §3.5 Checkpoint writer — one file per trace, append-only.
        # Falls back to a no-op sentinel when the logger is a test spy that
        # does not expose trace_id / log_dir (avoids coupling tests to I/O).
        try:
            from core.checkpoint import CheckpointWriter as _CPWriter
            _cp: Any = _CPWriter(trace_id=self.log.trace_id, log_dir=self.log.log_dir)
        except (AttributeError, ValueError):
            class _NoOpCP:
                """Silently drops all checkpoint calls."""
                def save_observe(self, **_kw: Any) -> None: pass
                def save_plan(self, **_kw: Any) -> None: pass
                def save_act(self, **_kw: Any) -> None: pass
                def save_respond(self, **_kw: Any) -> None: pass
                def save_paused(self, _data: dict[str, Any]) -> None: pass
            _cp = _NoOpCP()
        return _run_assumptions, _cp

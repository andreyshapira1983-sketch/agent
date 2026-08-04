"""Ворота цикла: четыре места, где ход заканчивается, не начавшись.

Правило оператора: «разбирай большие файлы на компактные подключаемые
модули — не дублируя и не искажая». Пятнадцатый кусок раскола `core/loop.py`.

Здесь живут ранние выходы: домен применимости (§7 ODD), политика уточнений
(§3), быстрый путь по эпизодам и отказ многофайлового разбора. Общее у них
одно — каждый может закончить ход ДО планирования, и каждый обязан вернуть
осмысленный ответ, а не молчание.

Приём переноса. Предыдущие куски уезжали выражением или под подстановкой; у
этих внутри `return`, и вот его переносить «как есть» нельзя: `return` из
помощника — это не выход из цикла, а всего лишь конец помощника. Поэтому
каждый метод отдаёт `str | None`: строка — «ход решён, вот ответ», `None` —
«я не при делах, продолжай». Вызывающий обязан проверить и вернуть сам.

Тела при этом ДОСЛОВНЫ: сверху добавлена только подпись, снизу — `return
None` на провал сквозь. Это и пинится в `tests/test_loop_gates_split.py`.

Два гейта — ODD и уточнения — намеренно чисто эвристические: ни модели, ни
ввода-вывода. Вопрос «можно ли за это вообще браться» нельзя задавать той же
модели, которая потом будет за это браться.

Класс подмешивается в ``AgentLoop``; состояние живёт на композированном цикле.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover — только для подписи
    from core.clarification_policy import ClarificationResult
    from core.operational_domain import DomainResult


class AgentLoopGates:
    """Ранние выходы: ход решён до планирования — или не решён.

    Члены ниже — объявления контракта хоста (``AgentLoop`` их создаёт в
    ``__init__``); присваиваний нет, поэтому во время выполнения ничего не
    создаётся и не затеняется. Тот же приём, что в ``loop_step_execution``.
    """

    if TYPE_CHECKING:  # pragma: no cover — только объявления
        log: Any
        memory: Any
        odd_enabled: Any
        clarification_enabled: Any
        episodic_replay: Any
        _stream_on_token: Any
        _last_best_similar_episode: Any
        _last_best_similar_score: Any
        # Как в `AgentLoop.__init__`: список или None до начала цикла.
        _cycle_findings: list[dict[str, Any]] | None

        # Объявляем ВЫЗЫВАЕМЫМИ атрибутами: заглушка-функция с пустым телом
        # читается анализаторами как «функция без return», и каждый вызов
        # ложно помечается E1111.
        _record_experience_memory: Any
        # Остались в `core/loop.py` намеренно: обе именуют `AgentLoop` прямо
        # в теле (константы порога живут на классе), и переезд означал бы
        # правку тела, а не перенос. Разрешаются по MRO, как и всё здесь.
        _fast_path_allows_replay: Any

    def _odd_gate(self, user_question: str) -> str | None:
        """Отказ, если запрос вне домена применимости, иначе `None`."""
        # 2b. Operational Design Domain gate (§7 ODD / B-05).
        # Pure-heuristic check — no LLM, no I/O.  When the request falls
        # outside the agent's operational domain (harmful/illegal, real
        # money, physical world, regulated advice, authority over people),
        # the loop stops here BEFORE any planning and returns an honest
        # refusal/escalation message instead of improvising an action.
        if self.odd_enabled:
            _odd = self._check_operational_domain(user_question)
            if _odd.blocks:
                self.log.log(
                    "out_of_domain",
                    {
                        "verdict": _odd.verdict,
                        "action": _odd.action,
                        "findings": [
                            {"kind": f.kind, "evidence": f.evidence, "confidence": f.confidence}
                            for f in _odd.findings
                        ],
                    },
                )
                self._stream_on_token = None
                return _odd.message
        return None

    def _clarification_gate(self, user_question: str) -> str | None:
        """Уточняющий вопрос, если запрос двусмыслен, иначе `None`."""
        # 2c. Clarification Policy (§3 Clarification Policy).
        # Pure-heuristic check — no LLM, no I/O.  When the question is
        # ambiguous about a destructive action the loop stops here and
        # returns the clarification question to the caller so the REPL
        # can surface it before any planning starts.
        if self.clarification_enabled:
            _clarif = self._check_clarification(user_question)
            if _clarif.should_ask:
                self.log.log(
                    "clarification_request",
                    {
                        "question": _clarif.question,
                        "findings": [
                            {"kind": f.kind, "evidence": f.evidence, "confidence": f.confidence}
                            for f in _clarif.findings
                        ],
                    },
                )
                self._stream_on_token = None
                return _clarif.question
        return None

    def _episodic_fast_path(
        self,
        user_question: str,
        *,
        file_hint: str | None,
        goal: Any,
        local_critique_active: bool,
    ) -> str | None:
        """Сохранённый ответ, если ход в точности повторяет прошлый, иначе `None`."""
        # ── Episodic fast path ───────────────────────────────────────────────────
        # Jaccard ≥ 0.85 AND quality ≥ 0.70 → serve the stored answer directly,
        # skipping both the planner LLM call and the synthesizer LLM call.
        # Conditions that disable the fast path:
        #   - episodic_replay is False (this agent may read experience memory
        #     but may not serve a stored answer in place of a fresh cycle —
        #     the unattended profile runs this way)
        #   - file_hint is set (the answer is tied to a specific file)
        #   - question starts with ':' (operator command)
        #   - full_answer is empty (episode from before this feature)
        #   - the cached episode used tools — its answer depends on the state of
        #     the environment (files, installed packages, command output) which
        #     may have changed since; only purely reasoned answers (no tools)
        #     are safe to replay verbatim.
        #   - local_critique_active (must critique current referent, not replay)
        _fp_ep = self._last_best_similar_episode
        _fp_score = self._last_best_similar_score
        if (
            self.episodic_replay
            and not local_critique_active
            and not file_hint
            and not user_question.strip().startswith(":")
            and self._fast_path_allows_replay(_fp_ep, _fp_score)
        ):
            self.log.log(
                "episodic_fast_path",
                {
                    "episode_id": _fp_ep.id,
                    "similarity": round(_fp_score, 4),
                    "quality": round(_fp_ep.answer_quality_score, 4),
                    "answer_chars": len(_fp_ep.full_answer),
                },
            )
            # A replay produces NO new evidence: nothing was fetched, nothing
            # was verified this cycle. Banking it as verified_chunks=1 minted
            # verification out of "it matched something in memory", and the
            # replay then looked as trustworthy as the answer it copied — a
            # self-reinforcing chain (MIR-041).
            #
            # unverified=1 rather than 0/0 on purpose: an empty chain scores
            # quality 1.0 (MIR-002), which would hand the replay top marks for
            # having no evidence at all. The source episode is named in
            # source_labels so the copy stays traceable to its origin.
            self._record_experience_memory(
                goal_description=goal.description,
                question=user_question,
                answer=_fp_ep.full_answer,
                tools_used=[],
                source_labels=[f"memory:{_fp_ep.id}"],
                verified_chunks=0,
                unverified_chunks=1,
                replan_exhausted=False,
                # No verifier ran on a replay, so none of it crashed. The
                # distinction matters: this flag means "the verifier threw",
                # not "no verification happened".
                verifier_failure=False,
            )
            if self.memory is not None:
                self.memory.record_turn(
                    question=user_question,
                    planner_reasoning="episodic fast path — cached answer",
                    tools_used=[],
                    artifact_labels=[f"memory:{_fp_ep.id}"],
                    answer=_fp_ep.full_answer,
                )
            self._stream_on_token = None
            return _fp_ep.full_answer
        return None

    def _multi_file_refusal(
        self,
        multi_file: dict[str, Any],
        *,
        user_question: str,
        file_hint: str | None,
        goal: Any,
    ) -> str | None:
        """Отказ многофайлового разбора, если он вынесен, иначе `None`."""
        if multi_file["kind"] == "refusal":
            answer = str(multi_file["message"])
            self.log.log("multi_file_review_refused", multi_file)
            self.log.log(
                "respond",
                {
                    "chars": len(answer),
                    "sources": [f"file:{file_hint}"] if file_hint else [],
                    "redactions": len(self._cycle_findings or []),
                    "attempts_used": 0,
                    "replan_exhausted": False,
                },
            )
            if self.memory is not None:
                turn = self.memory.record_turn(
                    question=user_question,
                    planner_reasoning="kernel multi-file review refusal",
                    tools_used=[],
                    artifact_labels=[],
                    answer=answer,
                )
                self.log.log(
                    "memory_write",
                    {
                        "session_id": self.memory.session_id,
                        "turn_id": turn.id,
                        "turn_index": turn.index,
                        "tools_used": turn.tools_used,
                        "labels": turn.artifact_labels,
                    },
                )
            self._record_experience_memory(
                goal_description=goal.description,
                question=user_question,
                answer=answer,
                tools_used=[],
                source_labels=[f"file:{file_hint}"] if file_hint else [],
                verified_chunks=0,
                unverified_chunks=1,
                replan_exhausted=False,
                # The refusal returns before planning, so verification never
                # started — again not a crash.
                verifier_failure=False,
            )
            self._stream_on_token = None
            return answer
        return None



    def _check_clarification(self, user_question: str) -> ClarificationResult:
        """Run the Clarification Policy (§3) — pure heuristic, no LLM."""
        from core.clarification_policy import check_clarification
        return check_clarification(user_question)

    def _check_operational_domain(self, user_question: str) -> DomainResult:
        """Run the Operational Design Domain gate (§7 ODD) — pure, no LLM."""
        from core.operational_domain import check_operational_domain
        return check_operational_domain(user_question)

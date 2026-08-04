"""Чтение памяти циклом: долгая, опытная, сводка.

Правило оператора: «разбирай большие файлы на компактные подключаемые модули».
Этот модуль — половина `core/loop_methods2.py`, которого больше нет.

Тот файл был не модулем, а отвалом: его сделал `core/incremental_splitter.py`,
резавший `core/loop.py` по бюджету строк, а не по смыслу, и имя `methods2`
было порядковым номером. Понять по нему, что внутри, было нельзя — при том,
что 10 из 11 его методов оказались одной темой: памятью цикла. Здесь чтение,
в `core/loop_memory_write.py` — запись и право на неё.

Всё чтение здесь СТРОГО read-only относительно памяти: выборка, форматирование
блока для промпта и сводка. Ни один метод отсюда ничего не записывает — иначе
запись обошла бы политику прав, которая живёт в модуле записи.

Методы перенесены дословно; сверка с историей — в
`tests/test_loop_memory_split.py`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.evidence_budget import MEMORY_CLOSE_TAG, MEMORY_OPEN_TAG
from core.smart_memory import (
    PROCEDURE_STATUSES,
    effective_completion,
    format_experience_context,
    is_usage_eligible,
)


def _merge_rejection_reasons(*reports: dict[str, int]) -> dict[str, int]:
    """Combine `rejected_by` maps from the components that did the rejecting.

    Retrieval is a chain of deciders — a use policy, then a scoring policy,
    then the loop's own filters — and each one knows the cause of its own
    drops. Merging what they report is the alternative to the observer
    inferring causes from `len(before) - len(after)`, which cannot separate a
    cap from a floor and, measured on the live store, mislabelled 4 of 6
    realistic questions.

    Reasons repeated across stages are summed: an episode ranked out by the
    store's cap and one ranked out by the loop's cap were both ranked out by
    a cap, and a reader looking for "how much did the caps cost me" wants one
    number. Absent reasons stay absent rather than becoming zeros.
    """
    merged: dict[str, int] = {}
    for report in reports:
        for reason, count in (report or {}).items():
            if count:
                merged[reason] = merged.get(reason, 0) + count
    return merged


class AgentLoopMemoryRead:
    """Выборка из памяти и её представление для промпта.

    Члены ниже — объявления контракта хоста (``AgentLoop`` их создаёт в
    ``__init__``); присваиваний нет, поэтому во время выполнения ничего не
    создаётся и не затеняется. Тот же приём, что в ``loop_step_execution``.
    """

    if TYPE_CHECKING:  # pragma: no cover — только объявления
        log: Any
        persistent_store: Any
        episodic_store: Any
        procedural_store: Any
        consolidation_store: Any
        retrieval_policy: Any
        knowledge_use_policy: Any
        last_role_context: Any

        # Берётся у соседней примеси: работает через MRO, но связь между
        # модулями обязана быть записана, иначе её видно только на прогоне.
        _durable_learning_suppressed: Any

    def _retrieve_persistent(self, question: str) -> str:
        """Pick + format relevant persistent records for prompt injection.

        Returns a `<long_term_memory>` XML block, or empty string when no
        store is wired, no records exist, or none score above threshold.
        """
        # MVP-14.1: stash the selected records so the evidence chain
        # can include them after run() finishes. Reset every cycle.
        self._last_persistent_records = []
        if self.persistent_store is None:
            return ""
        records = self.persistent_store.load()
        if not records:
            return ""
        use_report = self.knowledge_use_policy.filter(
            records,
            role_context=self.last_role_context,
            question=question,
        )
        self.log.log("knowledge_use_policy", use_report.to_log_payload())
        if not use_report.allowed:
            self.log.log(
                "persistent_memory_inject",
                {
                    "records_total": len(records),
                    "records_selected": 0,
                    "reason": "no records applicable to current role",
                    "role": self.last_role_context.role,
                    # The policy classified every rejection as it made it;
                    # `role_scope`, `quarantined` and `not_applicable` are
                    # three different diagnoses and used to be one number.
                    "rejected_by": use_report.rejected_by,
                },
            )
            return ""
        selection = self.retrieval_policy.select_with_report(use_report.allowed, question)
        selected = selection.selected
        rejected_by = _merge_rejection_reasons(use_report.rejected_by, selection.rejected_by)
        if not selected:
            self.log.log(
                "persistent_memory_inject",
                {
                    "records_total": len(records),
                    "records_selected": 0,
                    "reason": "no keyword overlap above threshold",
                    "role": self.last_role_context.role,
                    "records_applicable": len(use_report.allowed),
                    "rejected_by": rejected_by,
                },
            )
            return ""
        formatted = "\n".join(self.memory_record_lines(selected))
        self.log.log(
            "persistent_memory_inject",
            {
                "records_total": len(records),
                "records_selected": len(selected),
                "records_applicable": len(use_report.allowed),
                "ids": [r.id for r in selected],
                "chars": len(formatted),
                "role": self.last_role_context.role,
                # Same vocabulary as experience retrieval: reasons, not records.
                "rejected_by": rejected_by,
            },
        )
        self._last_persistent_records = list(selected)

        # Bump access stats on every retrieved record so the archive scorer
        # knows which records are actively useful (≠ junk that was saved but
        # never used again).
        if (
            not self._durable_learning_suppressed("access_stats")
            and self.persistent_store is not None
            and selected
        ):
            from datetime import datetime
            from datetime import timezone as _tz
            _now_dt = datetime.now(_tz.utc)
            for rec in selected:
                updated = rec.model_copy(update={
                    "access_count": rec.access_count + 1,
                    "last_accessed_at": _now_dt,
                })
                self.persistent_store.update(updated)

        return f"{MEMORY_OPEN_TAG}\n{formatted}\n{MEMORY_CLOSE_TAG}"

    def _retrieve_experience_memory(self, question: str) -> str:
        """Inject compact episodic/procedural memory into planning.

        This is deliberately separate from `<long_term_memory>`:
        persistent memory stores user-approved facts, while experience memory
        stores operational history and reusable workflows. It can guide the
        planner without becoming a source of factual claims in the final answer.

        Re-ask detection: if the current question is very similar (Jaccard ≥ 0.4)
        to the stored *question* field of a past episode, the user is likely asking
        AGAIN because the previous answer was insufficient.  A
        ``<repeat_question_hint>`` block is appended to signal this to the planner.
        """
        self._last_episode_records = []
        self._last_procedure_records = []
        self._last_best_similar_episode = None
        self._last_best_similar_score = 0.0
        # Holding the stores and being allowed to read them are separate
        # permissions. Returning early also leaves `_last_best_similar_episode`
        # unset, which structurally keeps the fast path from firing.
        # Counterfactual trace: WHY a record did not come back is where the
        # information is. `selected=0` alone cannot distinguish "nothing
        # matched" from "everything matched but was withheld", and those call
        # for opposite responses. Counted BY REASON, never per record — a
        # per-record trace would cost more than the retrieval it observes.
        rejected_by: dict[str, int] = {}
        if not getattr(self, "experience_retrieval", True):
            self.log.log(
                "experience_memory_inject",
                {
                    "episodes_selected": 0,
                    "procedures_selected": 0,
                    "episode_ids": [],
                    "procedure_ids": [],
                    "chars": 0,
                    "rejected_by": {"retrieval_disabled": 1},
                },
            )
            return ""
        if self.episodic_store is None and self.procedural_store is None:
            return ""
        # Only SUCCESSFUL episodes are fed back as reusable experience; a
        # `partial`/`failed` episode must not be surfaced as "what worked
        # before" (CORE-05/LPF-012 — the self-reinforcing loop). Curated
        # `lesson` episodes are kept regardless: they are learn-from-failure by
        # design. Over-fetch, then filter, then cap so up to 3 GOOD episodes
        # still surface even when some top matches were non-success.
        # `is_usage_eligible` is the second, independent filter: outcome asks
        # "did this go well", eligibility asks "is this episode allowed to
        # steer anything at all". Legacy and quarantined episodes stay stored
        # and auditable but never reach the planner.
        episodes = []
        readmitted = 0
        if self.episodic_store is not None:
            # `search_with_report` rather than `search`: the store drops
            # episodes for reasons only it can see (no token overlap, its own
            # cap), and counting only what it handed back is how
            # `selected=0, rejected_by={}` stayed reachable on 200 episodes.
            found = self.episodic_store.search_with_report(question, limit=6)
            rejected_by = _merge_rejection_reasons(rejected_by, found.rejected_by)
            for ep in found.episodes:
                if not (ep.outcome == "success" or "lesson" in ep.tags):
                    rejected_by["outcome"] = rejected_by.get("outcome", 0) + 1
                    continue
                # Checked here as well as at admission, not instead of it: the
                # stored `usage_eligible` bit was decided by whatever rule was
                # in force when the episode was banked, and this reader must
                # answer for its own use case. A `lesson` keeps its context
                # arm — surfacing a failure as a warning is the whole point of
                # the tag — but an ordinary episode has to have finished the
                # job before it may steer a later one.
                if "lesson" not in ep.tags and effective_completion(ep) != "achieved":
                    rejected_by["not_achieved"] = rejected_by.get("not_achieved", 0) + 1
                    continue
                if not is_usage_eligible(ep):
                    rejected_by["not_eligible"] = rejected_by.get("not_eligible", 0) + 1
                    continue
                if len(episodes) >= 3:
                    rejected_by["over_limit"] = rejected_by.get("over_limit", 0) + 1
                    continue
                episodes.append(ep)
        # ── Surface repair lessons for files mentioned in the question ────
        # search() gives a +50 boost to protected-tag episodes so they usually
        # appear in the top-3, but when the question contains a file path that
        # exactly matches a lesson's summary we fetch them explicitly as a
        # fallback — e.g. ":repair core/foo.py" should always see lessons
        # about core/foo.py even if the token overlap is otherwise low.
        if self.episodic_store is not None:
            lessons = self.episodic_store.search_by_tags(["lesson"], limit=5)
            q_lower = question.lower()
            # Extract path-like tokens: words containing "/" or ending in ".py"
            path_tokens = [
                w.strip("\"',:;()")
                for w in q_lower.split()
                if "/" in w or w.endswith(".py")
            ]
            for lesson in lessons:
                if (
                    is_usage_eligible(lesson)          # second door — same gate
                    and lesson not in episodes
                    and path_tokens
                    and any(tok in lesson.summary.lower() for tok in path_tokens)
                ):
                    episodes.append(lesson)
                    # This lesson was already charged to some rejection reason
                    # by the first pass, and which one is not knowable here
                    # without a per-record trace. Reported as its own number
                    # rather than guessed at and subtracted: with it, the
                    # reader reconciles as
                    # `selected - readmitted + sum(rejected_by) == candidates`.
                    readmitted += 1
        if self.procedural_store is not None:
            proc_result = self.procedural_store.search_with_report(question, limit=3)
            procedures = proc_result.procedures
            procedures_rejected_by = proc_result.rejected_by
        else:
            procedures = []
            procedures_rejected_by = {}
        block = format_experience_context(episodes=episodes, procedures=procedures)
        self.log.log(
            "experience_memory_inject",
            {
                "episodes_selected": len(episodes),
                "procedures_selected": len(procedures),
                "episode_ids": [ep.id for ep in episodes],
                "procedure_ids": [proc.id for proc in procedures],
                "chars": len(block),
                "rejected_by": rejected_by,
                # Why procedures did not surface — no longer a silent zero. Absent
                # means zero, like every reason key.
                **({"procedures_rejected_by": procedures_rejected_by}
                   if procedures_rejected_by else {}),
                # Absent means zero, like every reason key.
                **({"readmitted": readmitted} if readmitted else {}),
            },
        )
        self._last_episode_records = list(episodes)
        self._last_procedure_records = list(procedures)

        # ── Re-ask detection ──────────────────────────────────────────
        # Jaccard similarity on the question tokens only (not goal/summary).
        # High overlap (≥ 0.40) means the user is asking the SAME question
        # again — a strong signal that the previous answer was insufficient.
        _REPEAT_THRESHOLD = 0.40
        if self.episodic_store is not None:
            try:
                repeat_ep, repeat_score = self.episodic_store.find_most_similar(
                    question, threshold=_REPEAT_THRESHOLD
                )
                # Third door, and the most dangerous one: this feeds the fast
                # path, which returns a stored answer verbatim in place of a
                # real cycle. An ineligible match is dropped here rather than
                # at the fast-path gate, so re-ask hints cannot lean on it
                # either.
                if repeat_ep is not None and not is_usage_eligible(repeat_ep):
                    repeat_ep, repeat_score = None, 0.0
                # Store for fast-path and planner-cache checks in run().
                self._last_best_similar_episode = repeat_ep
                self._last_best_similar_score = repeat_score
                if repeat_ep is not None:
                    quality_pct = int(repeat_ep.answer_quality_score * 100)
                    quality_note = (
                        f"previous answer quality: {quality_pct}% verified — "
                        + ("HIGH" if quality_pct >= 70 else ("MEDIUM" if quality_pct >= 40 else "LOW"))
                    )
                    if quality_pct >= 70:
                        conclusion = (
                            "The previous answer was HIGH quality. "
                            "The user may be re-testing, want more detail, or verifying consistency. "
                            "You MAY confirm the previous answer if nothing has changed, "
                            "but try to add depth or perspective not present before."
                        )
                    else:
                        conclusion = (
                            "The previous answer was likely INSUFFICIENT or INCOMPLETE. "
                            "Do NOT repeat the same approach."
                        )
                    hint = (
                        "<repeat_question_hint>\n"
                        "WARNING: The user is asking a question that is very similar to a "
                        "previously answered one (Jaccard similarity "
                        f"{repeat_score:.2f} ≥ {_REPEAT_THRESHOLD}).\n"
                        f"Past episode: {repeat_ep.id} | outcome={repeat_ep.outcome}\n"
                        f"Past answer quality: {quality_note}\n"
                        f"Previous question: {repeat_ep.question[:200]}\n"
                        f"Previous answer summary: {repeat_ep.summary[:300]}\n"
                        f"CONCLUSION: {conclusion}\n"
                        "Action: try a different strategy, use additional tools, go "
                        "deeper, or explicitly acknowledge what was missing before.\n"
                        "</repeat_question_hint>"
                    )
                    self.log.log(
                        "repeat_question_detected",
                        {
                            "episode_id": repeat_ep.id,
                            "similarity": repeat_score,
                            "threshold": _REPEAT_THRESHOLD,
                            "past_outcome": repeat_ep.outcome,
                            "past_answer_quality": repeat_ep.answer_quality_score,
                            "past_question_chars": len(repeat_ep.question),
                        },
                    )
                    if block:
                        block = block + "\n\n" + hint
                    else:
                        block = hint
            except Exception:
                # Re-ask detection must never abort the main loop.
                pass

        return block

    def memory_record_lines(self, records: list) -> list[str]:
        """One formatted prompt line per record, wrapper tags neutralised.

        The single place a `<long_term_memory>` line is produced. The prompt
        builder needs the same lines to know where each record starts and ends
        after the evidence budget cuts the block; deriving them twice, or
        finding them again by pattern, is how a record that merely QUOTES a
        record-shaped line ended up being treated as a record boundary.

        Record content is partly agent-written and may quote the wrapper it is
        about to be placed in. A literal tag inside a record ends (or reopens)
        the block for the reading model, putting the rest of memory outside
        it — the same defence the local-critique path applies to
        `</analysis_target>` (`core/loop.py`).
        """
        lines: list[str] = []
        for record in records:
            line = self.retrieval_policy.format_for_prompt([record])
            # By PREFIX, not by exact tag: `<long_term_memory attr="x">` reads
            # as a boundary to the model just as well as the bare tag. This is
            # the rule the `<analysis_target` defence uses (`core/loop.py`).
            line = line.replace(f"{MEMORY_CLOSE_TAG[:-1]}", "&lt;/long_term_memory")
            line = line.replace(f"{MEMORY_OPEN_TAG[:-1]}", "&lt;long_term_memory")
            lines.append(line)
        return lines

    def _episodic_store_mtime(self) -> float:
        """Return the modification time of the episodic store file, or 0.0."""
        if self.episodic_store is None:
            return 0.0
        try:
            return self.episodic_store.path.stat().st_mtime
        except OSError:
            return 0.0

    def smart_memory_summary(self) -> dict[str, Any]:
        """Return local smart-memory counts for operator CLI commands."""
        episodes = self.episodic_store.load() if self.episodic_store else []
        procedures = self.procedural_store.load() if self.procedural_store else []
        reports = self.consolidation_store.load() if self.consolidation_store else []
        last_report = reports[-1].to_dict() if reports else None
        return {
            "episodic": {
                "path": str(self.episodic_store.path) if self.episodic_store else None,
                "episodes": len(episodes),
                "outcomes": {
                    outcome: sum(1 for ep in episodes if ep.outcome == outcome)
                    for outcome in ("success", "partial", "failed")
                },
            },
            "procedural": {
                "path": str(self.procedural_store.path) if self.procedural_store else None,
                "procedures": len(procedures),
                "statuses": {
                    status: sum(1 for proc in procedures if proc.status == status)
                    for status in PROCEDURE_STATUSES
                },
            },
            "consolidation": {
                "path": str(self.consolidation_store.path) if self.consolidation_store else None,
                "reports": len(reports),
                "last_report": last_report,
            },
        }

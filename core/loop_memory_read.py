"""Чтение памяти циклом: долгая, опытная, сводка.

Запись и право на неё — в `core/loop_memory_write.py`; отсюда пишутся только
счётчики доступа, и то через тот же гейт.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.bilingual_terms import recall_language_diagnostics
from core.evidence_budget import MEMORY_CLOSE_TAG, MEMORY_OPEN_TAG
from core.smart_memory import (
    PROCEDURE_STATUSES,
    effective_completion,
    format_experience_context,
    is_usage_eligible,
)
from core.topic_tokens import FLAT, TokenSalience, build_salience


def _family_appendix(family: list[str]) -> str:
    """Приложение к блоку опыта: продуктовые исходы тех же прогонов (MIR-184)."""
    if not family:
        return ""
    return (
        "\n⚠ Продуктовые исходы тех же прогонов (успех их не стирает):\n"
        + "\n".join(f"- {w}" for w in family)
    )


def _merge_rejection_reasons(*reports: dict[str, int]) -> dict[str, int]:
    """Sum `rejected_by` maps reported by each retrieval stage; absent reasons stay absent."""
    merged: dict[str, int] = {}
    for report in reports:
        for reason, count in (report or {}).items():
            if count:
                merged[reason] = merged.get(reason, 0) + count
    return merged


class AgentLoopMemoryRead:
    """Выборка из памяти и её представление для промпта; члены под TYPE_CHECKING — контракт хоста."""

    if TYPE_CHECKING:  # pragma: no cover — только объявления
        log: Any
        persistent_store: Any
        episodic_store: Any
        procedural_store: Any
        retrieval_policy: Any
        knowledge_use_policy: Any
        last_role_context: Any

        # Из соседних примесей (через MRO).
        _durable_learning_suppressed: Any
        _file_read_workspace_root: Any

    def _retrieve_persistent(self, question: str) -> str:
        """Pick relevant persistent records and return a `<long_term_memory>` block, or ""."""
        # Stashed for the evidence chain after run(); reset every cycle.
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
                    "rejected_by": use_report.rejected_by,
                },
            )
            return ""
        from core.self_knowledge import is_self_knowledge, self_knowledge_off_topic

        allowed = list(use_report.allowed)
        if self_knowledge_off_topic(question):  # предметной задаче — предмет, не разборы себя
            allowed = [r for r in allowed if not is_self_knowledge(getattr(r, "content", ""))]
        selection = self.retrieval_policy.select_with_report(allowed, question)
        selected = selection.selected
        rejected_by = _merge_rejection_reasons(use_report.rejected_by, selection.rejected_by)
        if len(allowed) < len(use_report.allowed):
            rejected_by["self_knowledge_off_topic"] = len(use_report.allowed) - len(allowed)
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
                    # Separates a language-gap miss from genuine absence.
                    **recall_language_diagnostics(question),
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
                "rejected_by": rejected_by,
            },
        )
        self._last_persistent_records = list(selected)

        # Access stats let the archive scorer tell used records from junk.
        if (
            not self._durable_learning_suppressed("access_stats")
            and self.persistent_store is not None
            and selected
        ):
            from datetime import datetime
            from datetime import timezone as _tz
            _now_dt = datetime.now(_tz.utc)
            # One rewrite total: `update` rewrites the whole file per record.
            self.persistent_store.update_many(
                rec.model_copy(update={
                    "access_count": rec.access_count + 1,
                    "last_accessed_at": _now_dt,
                })
                for rec in selected
            )

        return f"{MEMORY_OPEN_TAG}\n{formatted}\n{MEMORY_CLOSE_TAG}"

    def _question_salience(self) -> TokenSalience:
        """Редкость слова по вопросам памяти опыта — НЕ по речи оператора (MIR-105)."""
        store = getattr(self, "episodic_store", None)
        if store is None:
            return FLAT
        try:
            return build_salience(ep.question for ep in store.load() if ep.question)
        except (OSError, ValueError) as exc:
            # Без корпуса подбор считает штуками, но деградация видна в журнале.
            self.log.log("question_salience_unavailable", {"error": repr(exc)})
            return FLAT

    def _family_product_warnings(self, episodes: list) -> list[str]:
        """Продуктовые исходы тех же прогонов для эпизодов-успехов (MIR-184); сбой — в журнал."""
        if self.episodic_store is None or not episodes:
            return []
        try:
            from core.smart_memory import family_product_warnings

            return family_product_warnings(episodes, self.episodic_store.load())
        except Exception as exc:  # noqa: BLE001 — сшивка не роняет чтение
            self.log.log(
                "experience_family_join_failed",
                {"error": f"{type(exc).__name__}: {exc}"},
            )
            return []

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
        procedures, procedures_rejected_by = self._procedures_unless_workflow(question)
        block = self._code_checked(format_experience_context(episodes=episodes, procedures=procedures), "experience")
        family = self._family_product_warnings(episodes) if block else []
        block += _family_appendix(family)
        self.log.log(
            "experience_memory_inject",
            {
                "episodes_selected": len(episodes),
                "family_product_warnings": len(family),
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
        # Jaccard по различающим словам вопроса. 0.40 — НЕ рабочий порог:
        # у кандидата с неизмеренным качеством он падает до 0.30, а измеренного
        # качества нет ни у одного из 142 живых эпизодов (замер 2026-08-25).
        # Замер и границы: MIR-024 в docs/audit/MASTER_ISSUE_REGISTRY.md.
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
                # Store for the fast-path admission in run(). (This line used
                # to also claim "planner-cache checks": false — the planner
                # cache keys on (question hash, store mtime, file_hint) and
                # reads neither field. Verified by search 2026-08-08.)
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
                    block = block + "\n\n" + hint if block else hint
            except Exception:  # noqa: BLE001, S110 — reason stated above
                # Re-ask detection must never abort the main loop.
                pass

        text = self._workflow_block(question)
        return block + "\n\n" + text if block and text else (block or text)

    def _code_checked(self, block: str, source: str) -> str:
        """Ссылки памяти на код — сверены с кодом сейчас (core/code_citations.py)."""
        from core.code_citations import annotate_lines

        root = self._file_read_workspace_root() if hasattr(self, "_file_read_workspace_root") else None
        block, marked = annotate_lines(root, block)
        if marked:
            self.log.log("stale_code_citation", {"source": source, "lines": marked})
        return block

    def _workflows_for(self, question: str) -> list:
        """Шаблоны работы рода этого вопроса (core/workflow_memory.py); нет файла — нет шаблонов."""
        if self.procedural_store is None:
            return []
        from core.workflow_memory import FILE_NAME, WorkflowMemoryStore

        return WorkflowMemoryStore(self.procedural_store.path.parent / FILE_NAME).for_question(question)

    def _procedures_unless_workflow(self, question: str) -> tuple[list, dict[str, int]]:
        """Процедуры — только там, где у рода работы нет шаблона. Одно из двух, не оба.

        Шаблон строится из тех же процедур, и вместе они показывают один опыт
        дважды — это мешает сильнее, чем помогает каждый слой по отдельности.
        """
        if self.procedural_store is None:
            return [], {}
        workflows = self._workflows_for(question)
        if workflows:
            return [], {"covered_by_workflow": len(workflows)}
        found = self.procedural_store.search_with_report(question, limit=3, salience=self._question_salience())
        return found.procedures, found.rejected_by

    def _workflow_block(self, question: str) -> str:
        """Шаблоны работы по AWM в подсказку; нет шаблонов — нет блока, ход как прежде."""
        from core.workflow_memory import format_workflows

        workflows = self._workflows_for(question)
        if not workflows:
            return ""
        self.log.log("workflow_memory_inject",
                     {"workflow_ids": [w.id for w in workflows], "kinds": sorted({w.kind for w in workflows})})
        return format_workflows(workflows)

    def memory_record_lines(self, records: list) -> list[str]:
        """One formatted prompt line per record, wrapper tags neutralised.

        The single source of these lines: the prompt builder reuses them to find
        record boundaries. A literal wrapper tag inside a record would otherwise
        close the block early for the model.
        """
        lines: list[str] = []
        for record in records:
            line = self._code_checked(self.retrieval_policy.format_for_prompt([record]), "long_term")
            # By prefix: `<long_term_memory attr="x">` is a boundary to the model too.
            line = line.replace(f"{MEMORY_CLOSE_TAG[:-1]}", "&lt;/long_term_memory")
            line = line.replace(f"{MEMORY_OPEN_TAG[:-1]}", "&lt;long_term_memory")
            lines.append(line)
        return lines

    def smart_memory_summary(self) -> dict[str, Any]:
        """Return local smart-memory counts for operator CLI commands.

        Consolidation is computed on demand (MIR-044); `reports` is kept for
        display compatibility and is one or zero.
        """
        episodes = self.episodic_store.load() if self.episodic_store else []
        procedures = self.procedural_store.load() if self.procedural_store else []
        last_report = None
        if self.episodic_store is not None and self.procedural_store is not None:
            from core.smart_memory import consolidate_memory

            last_report = consolidate_memory(
                episodes=episodes, procedures=procedures
            ).to_dict()
        reports = [last_report] if last_report is not None else []
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
                "path": None,  # computed on demand (MIR-044); history archived
                "reports": len(reports),
                "last_report": last_report,
            },
        }

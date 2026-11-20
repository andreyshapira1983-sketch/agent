"""Loop-side memory reads: long-term, experience, summary.

    Writes and write permission live in `core/loop_memory_write.py`; only access
    counters are written here, through the same gate."""
from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

from core.bilingual_terms import recall_language_diagnostics
from core.evidence_budget import MEMORY_CLOSE_TAG, MEMORY_OPEN_TAG
from core.replay_freshness import READ_TOOLS, stale_inputs
from core.smart_memory import (
    PROCEDURE_STATUSES,
    effective_completion,
    format_experience_context,
    is_usage_eligible,
)
from core.topic_tokens import FLAT, TokenSalience, build_salience

_FAMILY_MAX_LINES = 5
_FAMILY_MAX_LINE_CHARS = 200
_LESSON_FALLBACK_LIMIT = 5


def _hint_field(text: Any, limit: int) -> str:
    """A memory field in a hint is data: ‹ › instead of < >, single line."""
    return (
        str(text or "")[:limit]
        .replace("<", "‹").replace(">", "›")
        .replace("\r", " ").replace("\n", " ")
    )


def _family_appendix(family: list[str]) -> str:
    """Appendix to the experience block: product outcomes of the same runs.

        Memory text is data: `<`/`>` replaced by ‹ ›, at most five lines, each truncated, so a record cannot close the block or read as an instruction."""
    if not family:
        return ""
    lines = [
        str(w)[:_FAMILY_MAX_LINE_CHARS].replace("<", "‹").replace(">", "›").replace("\n", " ")
        for w in family[:_FAMILY_MAX_LINES]
    ]
    more = len(family) - len(lines)
    return (
        "\n⚠ Продуктовые исходы тех же прогонов (успех их не стирает):\n"
        + "\n".join(f"- {w}" for w in lines)
        + (f"\n(и ещё {more})" if more > 0 else "")
    )


def _with_family(block: str, family: list[str]) -> str:
    """Family appendix goes INSIDE the experience block, before the closing tag."""
    appendix = _family_appendix(family)
    close = "</agent_experience_memory>"
    if appendix and block.endswith(close):
        return block[: -len(close)].rstrip("\n") + appendix + "\n" + close
    return block + appendix


def _experience_rejection(ep: Any) -> str | None:
    """Why an episode may not steer planning, or None when it may.

    Only successful episodes (or curated `lesson`s) are reused, else failures
    self-reinforce (CORE-05). The completion is rechecked: the stored bit
    reflects the rule at banking time.
    """
    if not (ep.outcome == "success" or "lesson" in ep.tags):
        return "outcome"
    if "lesson" not in ep.tags and effective_completion(ep) != "achieved":
        return "not_achieved"
    if not is_usage_eligible(ep):
        return "not_eligible"
    return None


def _merge_rejection_reasons(*reports: dict[str, int]) -> dict[str, int]:
    """Sum `rejected_by` maps reported by each retrieval stage; absent reasons stay absent."""
    merged: dict[str, int] = {}
    for report in reports:
        for reason, count in (report or {}).items():
            if count:
                merged[reason] = merged.get(reason, 0) + count
    return merged


class AgentLoopMemoryRead:
    """Memory retrieval and its prompt representation; TYPE_CHECKING members are the host contract."""

    if TYPE_CHECKING:  # pragma: no cover — только объявления
        log: Any
        persistent_store: Any
        episodic_store: Any
        procedural_store: Any
        retrieval_policy: Any
        knowledge_use_policy: Any
        last_role_context: Any

        # From sibling mixins (via MRO).
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
        if self_knowledge_off_topic(question):  # on a subject task: the subject, not self-analysis
            allowed = [r for r in allowed if not is_self_knowledge(getattr(r, "content", ""))]
        off_topic = len(use_report.allowed) - len(allowed)
        given = getattr(self, "memory_given_to_goal", None) or frozenset()
        fresh = [r for r in allowed if r.id not in given]
        given_before, allowed = len(allowed) - len(fresh), fresh
        selection = self.retrieval_policy.select_with_report(allowed, question)
        selected = selection.selected
        rejected_by = _merge_rejection_reasons(use_report.rejected_by, selection.rejected_by)
        if off_topic:
            rejected_by["self_knowledge_off_topic"] = off_topic
        if given_before:
            rejected_by["given_in_earlier_pass"] = given_before
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
            # One rewrite total, and only the access fields of the CURRENT records move: replacing whole records from the earlier copy erased a concurrent `superseded_by`.
            self.persistent_store.touch_access([rec.id for rec in selected], _now_dt)

        return f"{MEMORY_OPEN_TAG}\n{formatted}\n{MEMORY_CLOSE_TAG}"

    def _question_salience(self) -> TokenSalience:
        """Word rarity over experience-memory questions, NOT over the operator's speech."""
        store = getattr(self, "episodic_store", None)
        if store is None:
            return FLAT
        try:
            return build_salience(ep.question for ep in store.load() if ep.question)
        except (OSError, ValueError) as exc:
            # Without a corpus matching counts by pieces, but the degradation is visible in the journal.
            self.log.log("question_salience_unavailable", {"error": repr(exc)})
            return FLAT

    def _family_product_warnings(self, episodes: list) -> list[str]:
        """Product outcomes of the same runs for successful episodes; a failure goes to the journal."""
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
        """Inject compact episodic/procedural memory into planning (guides, never cited as fact).

        Appends a ``<repeat_question_hint>`` when a past episode's question is very similar.
        """
        self._last_episode_records = []
        self._last_procedure_records = []
        self._last_best_similar_episode = None
        self._last_best_similar_score = 0.0
        # Counted by reason, not per record: `selected=0` alone cannot tell
        # "nothing matched" from "matched but withheld".
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
        # Only successful episodes (or curated `lesson`s) are reused, else
        # failures self-reinforce (CORE-05). Over-fetch, filter, then cap at 3.
        episodes = []
        readmitted = 0
        if self.episodic_store is not None:
            # With report: only the store knows why it dropped an episode. The gate runs INSIDE the search, before the limit, so refused near-matches cannot hide an admissible one.
            found = self.episodic_store.search_with_report(
                question, limit=3, admit=_experience_rejection
            )
            rejected_by = _merge_rejection_reasons(rejected_by, found.rejected_by)
            episodes.extend(found.episodes)
        # Fallback: lessons about a file path named in the question surface even
        # when token overlap is low (e.g. ":repair core/foo.py").
        q_lower = question.lower()
        path_tokens = [
            w.strip("\"',:;()")
            for w in q_lower.split()
            if "/" in w or w.endswith(".py")
        ]
        if self.episodic_store is not None and path_tokens:
            # Path filter first, then the limit of 5: cutting first hid the sixth-newest lesson about a file.
            lessons = [
                lesson
                for lesson in self.episodic_store.search_by_tags(["lesson"], limit=sys.maxsize)
                if any(tok in lesson.summary.lower() for tok in path_tokens)
            ][:_LESSON_FALLBACK_LIMIT]
            for lesson in lessons:
                if (
                    is_usage_eligible(lesson)          # second door — same gate
                    and lesson not in episodes
                ):
                    episodes.append(lesson)
                    # Already counted in some rejection reason; reconcile as
                    # `selected - readmitted + sum(rejected_by) == candidates`.
                    readmitted += 1
        procedures, procedures_rejected_by = self._procedures_unless_workflow(question)
        block = self._code_checked(format_experience_context(episodes=episodes, procedures=procedures), "experience")
        family = self._family_product_warnings(episodes) if block else []
        block = _with_family(block, family)
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
                # Absent means zero, like every reason key.
                **({"procedures_rejected_by": procedures_rejected_by}
                   if procedures_rejected_by else {}),
                **({"readmitted": readmitted} if readmitted else {}),
            },
        )
        self._last_episode_records = list(episodes)
        self._last_procedure_records = list(procedures)

        # Re-ask detection. 0.40 is not a working threshold: without measured candidate quality it drops to 0.30.
        _REPEAT_THRESHOLD = 0.40
        if self.episodic_store is not None:
            try:
                repeat_ep, repeat_score = self.episodic_store.find_most_similar(
                    question, threshold=_REPEAT_THRESHOLD, admit=is_usage_eligible
                )  # filter BEFORE picking the best
                # Feeds the fast path (a stored answer returned verbatim), so an
                # ineligible match is dropped here, before hints can use it too.
                if repeat_ep is not None and not is_usage_eligible(repeat_ep):
                    repeat_ep, repeat_score = None, 0.0
                # For the fast-path admission in run().
                self._last_best_similar_episode, self._last_best_similar_score = AgentLoopMemoryRead._fast_path_candidate(
                    self, question, repeat_ep, repeat_score, _REPEAT_THRESHOLD)
                if repeat_ep is not None:
                    # A lesson may be eligible with no measured evidence, so the score can be None; guard `int(None * 100)`.
                    score = repeat_ep.answer_quality_score
                    quality_pct = None if score is None else int(score * 100)
                    quality_note = (
                        "previous answer quality: not measured (no evidence chunks)"
                        if quality_pct is None
                        else f"previous answer quality: {quality_pct}% verified — "
                        + ("HIGH" if quality_pct >= 70 else ("MEDIUM" if quality_pct >= 40 else "LOW"))
                    )
                    if quality_pct is not None and quality_pct >= 70:
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
                        # id and outcome are also data from memory.
                        f"Past episode: {_hint_field(repeat_ep.id, 120)}"
                        f" | outcome={_hint_field(repeat_ep.outcome, 40)}\n"
                        f"Past answer quality: {quality_note}\n"
                        f"Previous question: {_hint_field(repeat_ep.question, 200)}\n"
                        f"Previous answer summary: {_hint_field(repeat_ep.summary, 300)}\n"
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
            except Exception as exc:  # noqa: BLE001 — re-ask detection must never abort the main loop
                # A failure is journaled, not swallowed.
                self.log.log(
                    "repeat_question_detection_failed",
                    {"error": f"{type(exc).__name__}: {exc}"},
                )

        text = self._workflow_block(question)
        return block + "\n\n" + text if block and text else (block or text)

    def _fast_path_candidate(self, question: str, best: Any, score: float, threshold: float) -> tuple[Any, float]:
        """The best match the fast path could actually REPLAY.

        An eligible failed `lesson` is a good re-ask hint but must not hide a replayable runner-up."""
        replayable = getattr(self, "_fast_path_allows_replay", None)
        if best is None or not callable(replayable):
            return best, score

        def servable(e: Any) -> bool:  # replayable AND its read files unchanged
            return replayable(e, 1.0) and not AgentLoopMemoryRead._stale_replay_inputs(self, e)

        if servable(best):
            return best, score
        # The latest same ask acted, or its files changed: redo the work, never fall back to an older answer that skips the action.
        stale = AgentLoopMemoryRead._stale_replay_inputs(self, best)
        if set(getattr(best, "tools_used", ()) or ()) - READ_TOOLS or stale:
            if stale:
                self.log.log("episodic_fast_path_stale", {"episode_id": best.id, "changed_files": stale})
            return None, 0.0
        alt, alt_score = self.episodic_store.find_most_similar(
            question, threshold=threshold,
            admit=lambda e: is_usage_eligible(e) and servable(e),
        )
        return (alt, alt_score) if alt is not None else (best, score)

    def _stale_replay_inputs(self, episode: Any) -> list[str]:
        """Read files behind a stored answer that changed since; no workspace means none can be checked."""
        root = self._file_read_workspace_root() if hasattr(self, "_file_read_workspace_root") else None
        return stale_inputs(episode, root)

    def _code_checked(self, block: str, source: str) -> str:
        """Memory code references checked against the code now (core/code_citations.py)."""
        from core.code_citations import annotate_lines

        root = self._file_read_workspace_root() if hasattr(self, "_file_read_workspace_root") else None
        block, marked = annotate_lines(root, block)
        if marked:
            self.log.log("stale_code_citation", {"source": source, "lines": marked})
        return block

    def _workflows_for(self, question: str) -> list:
        """Work templates for this kind of question (core/workflow_memory.py); no file, no templates."""
        if self.procedural_store is None:
            return []
        from core.workflow_memory import FILE_NAME, WorkflowMemoryStore

        return WorkflowMemoryStore(self.procedural_store.path.parent / FILE_NAME).for_question(question)

    def _procedures_unless_workflow(self, question: str) -> tuple[list, dict[str, int]]:
        """Procedures only where the work kind has no template; one of the two, not both.

        The template is built from the same procedures, so showing both repeats the same experience."""
        if self.procedural_store is None:
            return [], {}
        workflows = self._workflows_for(question)
        if workflows:
            return [], {"covered_by_workflow": len(workflows)}
        found = self.procedural_store.search_with_report(question, limit=3, salience=self._question_salience())
        return found.procedures, found.rejected_by

    def _workflow_block(self, question: str) -> str:
        """Work templates (AWM) into the hint; no templates, no block."""
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

            Consolidation is computed on demand; `reports` is kept for display compatibility and is one or zero."""
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
                "path": None,  # computed on demand; history archived
                "reports": len(reports),
                "last_report": last_report,
            },
        }

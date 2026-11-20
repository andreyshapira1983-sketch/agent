"""Turn context gathered before planning (history, self-analysis classification, referent, long-term and experience memory), carved verbatim from ``core/loop.py``.

Only durable side effect, deliberate: long-term retrieval bumps `access_count` / `last_accessed_at` of the picked records
(`core/loop_memory_read.py`), gated by the `access_stats` sink allowlist; both polarities are pinned in
`tests/test_turn_context_read_c05.py`. State lives on the composed loop, not here.
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
from core.self_defect_reminder import format_open_self_defects, open_self_defects
from core.spend_report import load_spend_rows, spend_mirror_block


class AgentLoopContext:
    """Turn-context reads: history, self-analysis, referent, memory; members are host-contract declarations with no runtime assignments."""

    if TYPE_CHECKING:  # pragma: no cover — только объявления
        log: Any
        memory: Any
        model_router: Any
        user_profile_store: Any
        last_user_profile: Any
        last_self_analysis: Any
        last_referent_decision: Any
        _last_best_similar_episode: Any
        _last_best_similar_score: Any

        # Declared as CALLABLE attributes: an empty-body stub function is read by analyzers as "no return", and every call gets a false E1111.
        _retrieve_persistent: Any
        _retrieve_experience_memory: Any

        # Taken from a neighbouring mixin via the MRO; the cross-module link must be written down or it only shows at run time.
        _file_read_workspace_root: Any
        # `core/loop_sensor.py` helper, declared so the handlers below can report failures instead of swallowing them.
        _sensor_failed: Any

    def _retrieve_turn_context(
        self,
        user_question: str,
        *,
        file_hint: str | None,
    ) -> tuple[str, bool, str, str, str]:
        """History, critique, long-term and experience memory blocks, spend mirror.

                Order is contract: the referent is resolved BEFORE memory retrieval, because its verdict decides whether memory is injected at all.
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

        # Conversational correction / self-analysis classification, decided before planning because it changes the
        # evidence class the answer is judged against: a claim about THIS session's own exchange is backed by the
        # transcript, not by an external source. Deterministic and always on (no feature flag).
        _self_analysis = is_self_analysis_turn(
            user_question,
            has_prior_turn=bool(
                self.memory is not None and self.memory.recent_turns(1)
            ),
        )
        self.last_self_analysis = _self_analysis
        if _self_analysis.is_self_analysis:
            self.log.log("self_analysis_turn", _self_analysis.to_log_payload())

        # Referent resolver: shadow mode only logs; ``on`` enables the local-critique path.
        self._maybe_resolve_referent(user_question, file_hint=file_hint)
        local_critique_active = (
            referent_resolver_mode() == "on"
            and self.last_referent_decision is not None
            and is_local_critique_eligible(self.last_referent_decision)
        )
        if local_critique_active:
            _rd = self.last_referent_decision
            assert _rd is not None and _rd.primary is not None  # noqa: S101 — type narrowing, guarded above
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

        # Persistent memory retrieval: pick a few long-term records sharing keywords with the question and inject
        # them as a <long_term_memory> block; local-critique turns suppress default LTM/episodic injection.
        if local_critique_active:
            persistent_block = ""
            experience_block = ""
            self._last_best_similar_episode = None
            self._last_best_similar_score = 0.0
        else:
            persistent_block = self._retrieve_persistent(user_question)
            experience_block = self._retrieve_experience_memory(user_question)

        # Spend mirror: a fifth element, not a part of the experience block (own contract and tests). Own
        # "spent -> got" pairs as data; empty journals take no prompt space; suppressed on local-critique turns.
        spend_block = "" if local_critique_active else self._spend_mirror()
        # Own open defect records go into the same "about myself" element, not the experience block.
        self._self_defects_block = "" if local_critique_active else self._self_defects()
        spend_block = "\n\n".join(p for p in (spend_block, self._self_defects_block) if p)

        return history, local_critique_active, persistent_block, experience_block, spend_block

    def _self_defects(self) -> str:
        """Open registry records of the agent's own defects, as a turn block."""
        try:
            rows = open_self_defects(self._file_read_workspace_root())
        except Exception as exc:  # noqa: BLE001 — напоминание не вправе ронять ход
            self.log.log("self_defects_unavailable", {"error": repr(exc)[:200]})
            return ""
        self.log.log("self_defects_inject", {
            "count": len(rows), "titles": [str(r.get("title") or r.get("issue"))[:80] for r in rows],
        })
        return format_open_self_defects(rows)

    def _spend_mirror(self) -> str:
        """Spend-mirror block from the live journals; empty without a work folder."""
        try:
            usage_rows, ledger_rows = load_spend_rows(self._file_read_workspace_root())
            spend = spend_mirror_block(usage_rows=usage_rows, ledger_rows=ledger_rows)
        except Exception as exc:  # noqa: BLE001 — зеркало не вправе ронять ход
            # Never silent: an operator reading logs must see that the mirror was absent and why.
            self.log.log("spend_mirror_unavailable", {"error": repr(exc)[:200]})
            spend = ""
        roster = self._model_roster_block()
        return "\n\n".join(part for part in (spend, roster) if part)

    def _model_roster_block(self) -> str:
        """Eyes on the agent's own models: providers, key present/absent, roles, health, price, daily spend, remaining cap. Facts only.

                Without a router with a journal there is no block: a stub router (`ModelRouter.single`) has none,
                and reading the developer's environment instead would leak it into clean sandboxes.
        """
        usage_ledger = getattr(getattr(self, "model_router", None), "usage_ledger", None)
        if usage_ledger is None:
            return ""
        try:
            from core.model_roster import model_roster, model_roster_block

            budget_ledger = getattr(usage_ledger, "budget_ledger", None)
            return model_roster_block(model_roster(
                usage_ledger=usage_ledger, budget_ledger=budget_ledger,
                routing_policy=getattr(self.model_router, "routing_policy", None),
                workspace=self._file_read_workspace_root(),
            ))
        except Exception as exc:  # noqa: BLE001 — глаз не вправе ронять ход
            self.log.log("model_roster_unavailable", {"error": repr(exc)[:200]})
            return ""

    def _maybe_resolve_referent(
        self,
        user_question: str,
        *,
        file_hint: str | None,
    ) -> None:
        """Shadow/on referent resolution. Shadow logs only; ``on`` enables the local-critique path."""
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
            # True when enabling ``on`` would change the answer path.
            payload["would_change_answer"] = eligible
            self.log.log("referent_decision", payload)
        except Exception as exc:  # noqa: BLE001 — reason stated above
            # Observability must never abort the run, and its failure must not be invisible: a broken resolver logs
            # NOTHING and leaves the decision `None`, which looks like an unresolved referent.
            # The `None` stays: it is the fail-safe that keeps a broken resolver from enabling a path it cannot support.
            self.last_referent_decision = None
            self._sensor_failed("referent_resolution", exc)


    def _open_run(self, user_question: str) -> tuple[Any, Any]:
        """Profile, assumption registry and checkpoint writer: everything opened ONCE before the first phase.

                Returns the registry and the writer; the rest lands on loop fields. The registry starts CLEAN:
                the assumption store is an archive, not an input.
        """
        # Load the user profile for this cycle.
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

        # Create a fresh AssumptionRegistry seeded from the question, passing the profile's known language so
        # extraction uses the higher-confidence profile signal.
        _run_assumptions = AssumptionRegistry(
            run_id=getattr(self.log, "trace_id", ""),
        )
        # The assumption store is an ARCHIVE, not an active input: archived rows return only via explicit
        # retrieval (cross-turn auto-restore leaked assumptions between unrelated goals).
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
        except Exception as exc:  # noqa: BLE001 — reason stated above
            # Reported, not swallowed: `assumptions_registered` fires later and only for a non-empty registry, so a
            # crash here would look like a question with nothing to assume.
            self._sensor_failed("assumption_extraction", exc)

        # Checkpoint writer: one file per trace, append-only; falls back to a no-op sentinel when the logger is
        # a test spy without trace_id / log_dir.
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

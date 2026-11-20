"""Evidence chain assembly, moved verbatim out of ``core/loop.py``."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from core.evidence import (
    ProvenanceChain,
    evidence_from_memory_record,
    evidence_from_prior_turn,
    make_evidence,
)
from core.knowledge_pipeline import KnowledgePipelineResult
from core.source_ranker import SourceRankingReport, rank_chain


@dataclass(frozen=True)
class CatalogueResult:
    """What cataloguing a chain produces, for the caller to act on."""

    ranking: SourceRankingReport
    knowledge: KnowledgePipelineResult


#: Evidence kinds that do NOT come from tools: the agent already had them before the turn. Cataloguing them would be harmful: its own memory recorded as new knowledge is self-confirmation (an own record is not an independent witness).
_NON_TOOL_EVIDENCE_KINDS: frozenset[str] = frozenset({
    "memory", "user_explicit", "session_dialogue", "llm_claim", "runtime",
    "sensor",  # his own roster/spend blocks: evidence for the verifier, not new knowledge
})


def chain_has_tool_evidence(chain: Any) -> bool:
    """Whether anything obtained by a tool appeared on this turn."""
    return any(
        getattr(ev, "kind", "") not in _NON_TOOL_EVIDENCE_KINDS
        for ev in getattr(chain, "evidences", ())
    )


class AgentLoopEvidenceChain:
    """Chain top-up: memory, working artifacts, dialogue."""

    if TYPE_CHECKING:  # pragma: no cover — declarations only
        log: Any
        registry: Any
        memory: Any
        knowledge_pipeline: Any
        knowledge_auto_write: Any
        source_registry_store: Any
        last_source_ranking: Any
        last_source_registry: Any
        last_knowledge_pipeline: Any
        _termination_guard: Any
        persistent_store: Any
        last_provenance: Any
        last_self_analysis: Any
        _last_persistent_records: Any

        # Declared as a CALLABLE attribute: a stub function with an empty body is read by analyzers as "no return" and every call is falsely flagged E1111.
        _sensor_failed: Any
        _knowledge_remember_batch: Any
        _unattended_run: Any

    def _catalogue_chain(
        self,
        chain: ProvenanceChain,
        *,
        question: str,
        may_knowledge: bool,
        may_source_registry: bool,
    ) -> CatalogueResult:
        """Rank the chain and run the knowledge pipeline over it.

        **This is not a pure computation and must not be described as one.**
        `knowledge_pipeline.run` catalogues sources and, when
        `auto_write_memory` is on and the claim passes `require_verified`,
        **writes to long-term memory**. It also persists the source registry
        when `source_store` is supplied. Both are governed by the two
        permissions this method takes and by `_unattended_run()`, and both
        happen inside this call.
        """
        ranking = rank_chain(chain, question=question)
        knowledge = self.knowledge_pipeline.run(
            chain,
            ranking=ranking,
            source_store=self.source_registry_store if may_source_registry else None,
            remember=self._knowledge_remember_batch() if may_knowledge else None,
            auto_write_memory=(
                self.knowledge_auto_write if may_knowledge else False
            ),
            # Unattended runs demand corroboration; a human at the REPL can judge a single source.
            require_verified=self._unattended_run(),
            # The turn does not make web finds into memory facts (two worlds).
            admit_internet=False,
        )
        return CatalogueResult(ranking=ranking, knowledge=knowledge)

    def _fold_subagent_evidence(self, chain: ProvenanceChain) -> None:
        """The pages a subagent READ cross the boundary beside what it SAID.

        The spawn tool stashes the child's external evidences; they are folded here once, with origin `subagent:<name>:<child trace>`, so honest negative reports are not booked as "subagent_asserted" and suppressed.
        """
        from core.evidence import Evidence

        # No spawn tool in this registry: nothing to carry or journal (a lookup, not a swallowed failure).
        tool = next((t for t in self.registry.list() if t.name == "spawn_subagent"), None)
        if tool is None:
            return
        raw = list(getattr(tool, "last_child_evidences", None) or [])
        tool.last_child_evidences = []
        carried: list[Evidence] = []
        for d in raw:
            try:
                carried.append(Evidence.from_dict(dict(d)))
            except Exception as exc:  # noqa: BLE001 — a bad row is named, the rest still crosses
                self.log.log("subagent_evidence_skipped", {"error": repr(exc)[:200]})
        for ev in carried:
            chain.add(ev)
        if carried:
            self.log.log("subagent_evidence_carried", {
                "count": len(carried), "kinds": sorted({e.kind for e in carried}),
            })

    def _fold_sensor_evidence(self, chain: ProvenanceChain, spend_block: str) -> None:
        """Blocks of the agent's own sensors (model roster, spend mirror) as evidence with citation `[sensor:<name>]`.

        A separate method: `_fold_evidence_chain` is pinned verbatim to the split.
        """
        if not spend_block or not spend_block.strip():
            return
        from core.evidence import evidence_from_sensor_block

        for name, block in _sensor_blocks(spend_block):
            try:
                chain.add(evidence_from_sensor_block(name=name, content=block))
            except Exception as exc:  # noqa: BLE001 — chain assembly does not drop the turn; the skip is named
                self.log.log("sensor_evidence_skipped", {"sensor": name, "error": repr(exc)[:200]})

    def _fold_evidence_chain(
        self,
        chain: ProvenanceChain,
        *,
        persistent_block: str,
    ) -> None:
        """Add to the chain what did not come through plan steps."""
        if persistent_block and self.persistent_store is not None:
            # `persistent_block` was built from a small set of records in `_retrieve_persistent`; replay that retrieval by re-asking the store for the keyword match.
            # The guard is PER RECORD, not around the loop: one failed conversion must not drop every record after it, or the verifier judges a truncated chain and citations come back `cited_but_unmatched`.
            for rec in self._last_persistent_records:
                try:
                    chain.add(
                        evidence_from_memory_record(
                            record_id=rec.id,
                            content=rec.content,
                            source=getattr(rec, "source", None),
                            created_at=getattr(rec, "created_at", None),
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    # Chain assembly must NEVER abort the run, but a dropped record is reported, since silent truncation looks like an ordinary evidence shortfall.
                    self.log.log(
                        "memory_evidence_skipped",
                        {
                            "record_id": getattr(rec, "id", None),
                            "error": type(exc).__name__,
                            "message": str(exc)[:200],
                        },
                    )

        # Fold Working Memory (cached tool outputs from prior turns) into the chain so the Verifier can resolve [memory:...] citations; artefact label and turn index go in source_id for the token-overlap fallback.
        if self.memory is not None:
            for _art in self.memory.artifacts.values():
                try:
                    _label = str(_art.get("label", ""))
                    _tidx = int(_art.get("turn_index", 0))
                    _output = _art.get("output")
                    if _output is None or not _label:
                        continue
                    # Sanitise the label for source_id: replace `:` with `_` so the citation prefix parser is not confused.
                    _sid_label = _label.replace(":", "_")
                    chain.add(make_evidence(
                        kind="memory",
                        source_id=f"memory:working_turn_{_tidx}_{_sid_label}",
                        obtained_via="working_memory",
                        claim=f"Cached tool output from turn {_tidx}: {_label}",
                        excerpt=str(_output)[:500],
                        confidence=0.85,
                    ))
                except Exception as exc:  # noqa: BLE001 — observing sensor: failure is journaled, the turn goes on
                    self._sensor_failed("working_memory_evidence", exc)

        # Verbatim record of the exchange; the self-correction-only gate is gone, and verifier_core limits `dialogue_supported` to chunks about the exchange itself.
        _recent_turns = self.memory.recent_turns(3) if self.memory is not None else []
        if _recent_turns:
            _dialogue_added = 0
            for _turn in _recent_turns:
                try:
                    chain.add(
                        evidence_from_prior_turn(
                            turn_id=_turn.id,
                            turn_index=_turn.index,
                            question=_turn.question,
                            answer=_turn.answer,
                        )
                    )
                    _dialogue_added += 1
                except Exception as exc:  # noqa: BLE001
                    self.log.log(
                        "dialogue_evidence_skipped",
                        {
                            "turn_id": getattr(_turn, "id", None),
                            "error": type(exc).__name__,
                            "message": str(exc)[:200],
                        },
                    )
            self.log.log(
                "dialogue_evidence_admitted",
                {
                    "turns": _dialogue_added,
                    # The reason is always "history exists"; the self-analysis mark is a separate field (run analysis tells a reproach turn apart).
                    "reason": "session_history_present",
                    "self_analysis": getattr(self.last_self_analysis, "is_self_analysis", False),
                },
            )

        # Store the chain on the agent so tests / Verifier code can consult it after `run()` returns.
        self.last_provenance = chain

    def _mark_own_cached_evidence(self, chain: ProvenanceChain) -> None:
        """A cached result is as independent as the call that made it, no more.

        A cached `memory_recall` or read of the agent's own data/ must not count as independent proof. Kept apart from `_fold_evidence_chain`, whose body is pinned verbatim (tests/test_loop_evidence_chain_split.py).
        """
        from dataclasses import replace

        from core.evidence import evidence_from_tool_result, evidence_is_independent

        memory = getattr(self, "memory", None)
        if memory is None:
            return
        own_labels: set[str] = set()
        for art in list(memory.artifacts.values()):
            try:
                orig = evidence_from_tool_result(
                    tool_name=str(art.get("tool") or ""),
                    arguments=dict(art.get("arguments") or {}),
                    output=art.get("output"),
                )
            except Exception as exc:  # noqa: BLE001 — a sensor: logged, the turn goes on
                self._sensor_failed("working_memory_origin", exc)
                continue
            if orig is not None and not evidence_is_independent(orig):
                own_labels.add(f"memory:working_turn_{int(art.get('turn_index', 0))}_"
                               f"{str(art.get('label', '')).replace(':', '_')}")
        if own_labels:
            chain.evidences[:] = [
                replace(ev, origin="agent-own")
                if ev.obtained_via == "working_memory" and ev.source_id in own_labels else ev
                for ev in chain.evidences
            ]

    def _rank_and_catalog_evidence(
        self,
        chain: ProvenanceChain,
        *,
        user_question: str,
        artifacts: dict[str, dict[str, Any]],
        cheap_path_active: bool,
        may_knowledge: bool,
        may_source_registry: bool,
    ) -> tuple[bool, SourceRankingReport, Any]:
        """Shadow sensor verdict, source ranking and registry."""
        # Keyword detector for premature completion, RETAINED FOR SHADOW COMPARISON ONLY: low recall (1/12) on phrasings that demand a tool. The obligation check that replaces it runs after composition; this verdict rides in its event for comparison.
        _premature_keyword_fired = False
        try:
            _pc = self._termination_guard.check_completion(
                question=user_question,
                chain_size=len(chain),
                had_any_artifacts=bool(artifacts),
            )
            if _pc is not None:
                _premature_keyword_fired = True
                self.log.log(
                    "premature_completion_risk", _pc.to_log_payload()
                )
        except Exception as exc:  # noqa: BLE001 — observing sensor: failure is journaled, the turn goes on
            self._sensor_failed("premature_completion_risk", exc)

        self.log.log(
            "evidence_collected",
            {
                "count": len(chain),
                "kinds": sorted({ev.kind for ev in chain.evidences}),
                "chain": chain.to_log_payload(),
            },
        )
        if cheap_path_active and not chain_has_tool_evidence(chain):
            # Cheap path: nothing was FETCHED this turn, so the knowledge pipeline and source-registry build have nothing new to catalog.
            # The condition checks what it claims (a memory-only chain is not new knowledge: running the pipeline would bank the agent's own record as knowledge, self-confirmation). Ranked all the same, so the event shows the chain carried nothing new rather than went unexamined.
            source_ranking = rank_chain(chain, question=user_question)
            self.last_source_ranking = source_ranking
            self.log.log("source_ranking", source_ranking.to_log_payload())
            source_registry = self.last_source_registry
            self.log.log(
                "knowledge_pipeline_skipped",
                {"reason": "cheap_path_no_tool_evidence", "chain_size": len(chain)},
            )
        else:
            catalogued = self._catalogue_chain(
                chain,
                question=user_question,
                may_knowledge=may_knowledge,
                may_source_registry=may_source_registry,
            )
            source_ranking = catalogued.ranking
            self.last_source_ranking = source_ranking
            self.log.log("source_ranking", source_ranking.to_log_payload())
            knowledge_result = catalogued.knowledge
            source_registry = knowledge_result.registry
            self.last_source_registry = source_registry
            self.log.log("source_registry", source_registry.to_log_payload())
            self.last_knowledge_pipeline = knowledge_result
            self.log.log("knowledge_pipeline", knowledge_result.to_log_payload())

        return _premature_keyword_fired, source_ranking, source_registry


def _sensor_blocks(text: str) -> list[tuple[str, str]]:
    """Split the spend mirror text into tagged blocks: `<model_roster>...` becomes ("model_roster", ...); untagged text is one block named "spend_mirror"."""
    import re

    found = re.findall(r"<([a-z_]+)>(.*?)</\1>", text, flags=re.DOTALL)
    if found:
        return [(name, f"<{name}>{body}</{name}>") for name, body in found]
    return [("spend_mirror", text)]

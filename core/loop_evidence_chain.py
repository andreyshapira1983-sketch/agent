"""Досборка цепочки улик — вырезано из ``core/loop.py`` дословно."""
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


#: Виды улик, которые НЕ приходят от инструментов: они уже были у агента до
#: хода. Каталогизировать их незачем, и хуже — вредно: своя же память,
#: записанная как новое знание, есть самоподтверждение (доктрина MIR-046 —
#: собственная запись не независимый свидетель).
_NON_TOOL_EVIDENCE_KINDS: frozenset[str] = frozenset({
    "memory", "user_explicit", "session_dialogue", "llm_claim", "runtime",
    "sensor",  # his own roster/spend blocks: evidence for the verifier, not new knowledge
})


def chain_has_tool_evidence(chain: Any) -> bool:
    """Появилось ли на этом ходе хоть что-то, добытое инструментом."""
    return any(
        getattr(ev, "kind", "") not in _NON_TOOL_EVIDENCE_KINDS
        for ev in getattr(chain, "evidences", ())
    )


class AgentLoopEvidenceChain:
    """Досборка цепочки: память, рабочие артефакты, диалог."""

    if TYPE_CHECKING:  # pragma: no cover — только объявления
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

        # Объявляем ВЫЗЫВАЕМЫМ атрибутом: заглушка-функция с пустым телом
        # читается анализаторами как «функция без return», и каждый вызов
        # ложно помечается E1111.
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
            # Unattended runs demand corroboration; a human at the REPL can
            # judge a single source themselves.
            require_verified=self._unattended_run(),
        )
        return CatalogueResult(ranking=ranking, knowledge=knowledge)

    def _fold_subagent_evidence(self, chain: ProvenanceChain) -> None:
        """The pages a subagent READ cross the boundary beside what it SAID.
        Work order 1, pass 2 (2026-09-05): three subagents fetched real pages
        (HTTP 429, an «unsupported» page, dynamic shells); the parent held only
        their prose and a count, the verifier booked every claim about them
        «subagent_asserted», and the honest negative report was suppressed.
        The spawn tool stashes the child's external evidences; they are folded
        here, once, with origin `subagent:<name>:<child trace>`."""
        from core.evidence import Evidence

        # No spawn tool in this registry: nothing to carry, nothing to journal
        # (a lookup, not a swallowed failure — the silence ratchet counts those).
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
        """Блоки собственных сенсоров (реестр моделей, зеркало трат) — улика с
        цитатой `[sensor:<name>]`. Экзамен 2026-09-04, ход 3: 15 фактов из
        <model_roster> судья счёл «утверждениями пользователя» и стёр ответ.
        Отдельный метод, а не строки внутри `_fold_evidence_chain`: тот
        перенесён дословно и сверяется с историей."""
        if not spend_block or not spend_block.strip():
            return
        from core.evidence import evidence_from_sensor_block

        for name, block in _sensor_blocks(spend_block):
            try:
                chain.add(evidence_from_sensor_block(name=name, content=block))
            except Exception as exc:  # noqa: BLE001 — сборка цепочки не роняет ход; пропуск назван
                self.log.log("sensor_evidence_skipped", {"sensor": name, "error": repr(exc)[:200]})

    def _fold_evidence_chain(
        self,
        chain: ProvenanceChain,
        *,
        persistent_block: str,
    ) -> None:
        """Досыпать в цепочку то, что пришло не через шаги плана."""
        if persistent_block and self.persistent_store is not None:
            # `persistent_block` was built from a small set of records
            # in `_retrieve_persistent`; we replay that retrieval cheaply
            # by re-asking the store for the keyword match.
            #
            # The guard is PER RECORD, not around the loop (MIR-061). With the
            # try outside, one record that failed to convert abandoned the
            # whole loop and every record after it vanished from the chain
            # silently — measured at 1 of 5 arriving — while this comment
            # claimed the loop completed normally. The verifier then judged the
            # answer against a truncated chain, so citations that should have
            # resolved came back `cited_but_unmatched` for a reason unrelated
            # to the answer. The adjacent working-artifact loop below has
            # always had the correct granularity; these two must not drift
            # apart again.
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
                    # Defence-in-depth: chain assembly must NEVER abort the
                    # run. But a dropped record is reported rather than
                    # swallowed — silently, the truncation is indistinguishable
                    # from an ordinary evidence shortfall.
                    self.log.log(
                        "memory_evidence_skipped",
                        {
                            "record_id": getattr(rec, "id", None),
                            "error": type(exc).__name__,
                            "message": str(exc)[:200],
                        },
                    )

        # MVP-14.1b — fold Working Memory (cached tool outputs from prior
        # turns) into the chain so the Verifier can resolve [memory:…]
        # citations that reference conversation-history artefacts.
        # The LLM may generate citation bodies like `turn_3_test_results`;
        # we expose the artefact label and turn index in source_id so the
        # token-overlap fallback has material to work with.
        if self.memory is not None:
            for _art in self.memory.artifacts.values():
                try:
                    _label = str(_art.get("label", ""))
                    _tidx = int(_art.get("turn_index", 0))
                    _output = _art.get("output")
                    if _output is None or not _label:
                        continue
                    # Sanitise label for use in source_id: replace `:` with
                    # `_` so it doesn't confuse the citation prefix parser.
                    _sid_label = _label.replace(":", "_")
                    chain.add(make_evidence(
                        kind="memory",
                        source_id=f"memory:working_turn_{_tidx}_{_sid_label}",
                        obtained_via="working_memory",
                        claim=f"Cached tool output from turn {_tidx}: {_label}",
                        excerpt=str(_output)[:500],
                        confidence=0.85,
                    ))
                except Exception as exc:  # noqa: BLE001 — наблюдательный сенсор: сбой журналируется, ход не ломается
                    self._sensor_failed("working_memory_evidence", exc)

        # Issue #119 — дословная запись обмена. Гейт «только самокоррекция»
        # снят (прогон 2026-08-03: честная ссылка звалась выдумкой); держит
        # verifier_core — `dialogue_supported` лишь чанку про сам обмен.
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
                    # Причина всегда «история есть»; отметку самоанализа держим
                    # отдельным полем — разбор прогонов различает ход-упрёк.
                    "reason": "session_history_present",
                    "self_analysis": getattr(self.last_self_analysis, "is_self_analysis", False),
                },
            )

        # Store the chain on the agent so tests / future Verifier code
        # can consult it after `run()` returns.
        self.last_provenance = chain

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
        """Теневой вердикт сенсора, ранжирование источников и реестр."""
        # MAST FM-3.1 — premature completion risk, keyword detector.
        # RETAINED FOR SHADOW COMPARISON ONLY. It is no longer the source of
        # truth: measured at 1/12 recall on phrasings that unambiguously demand
        # a tool, and it fires on «объясни разницу…» because `разниц` is a
        # diff-tool keyword. The obligation check that replaces it runs after
        # composition, and this verdict is carried into its event so the two can
        # be compared on real traffic.
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
        except Exception as exc:  # noqa: BLE001 — наблюдательный сенсор: сбой журналируется, ход не ломается
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
            # Cheap path: nothing was FETCHED this turn, so the knowledge
            # pipeline and source-registry build have nothing new to catalog.
            #
            # H-05 (Therac-25 class, docs/audit/HISTORICAL_FAILURE_LEDGER.md).
            # This used to read «the chain is empty (no tools ran)» and decide
            # on the cheap-path flag alone. Measured end-to-end on the turn
            # «привет» with one persistent record: the flag is set and the
            # chain holds ONE `memory` evidence — memory is folded in
            # (`_fold_evidence_chain`) BEFORE this decision. The premise was
            # false while the behaviour was right, which is exactly the shape
            # that killed the Therac patients: the machine's statement about
            # itself diverged from what it was.
            #
            # Behaviour is deliberately unchanged. Running the pipeline over a
            # memory-only chain would bank the agent's own record as new
            # knowledge — self-confirmation, the doctrine MIR-046 states. What
            # changed is that the condition now checks the thing it claims, so
            # the two cannot drift apart silently.
            # Ranked all the same: the event is what tells a reader the chain
            # carried nothing new rather than went unexamined.
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
    """Split the spend mirror text into its tagged blocks: `<model_roster>…`
    becomes ("model_roster", …), `<spend_mirror>…` likewise; untagged text is
    one block named «spend_mirror»."""
    import re

    found = re.findall(r"<([a-z_]+)>(.*?)</\1>", text, flags=re.DOTALL)
    if found:
        return [(name, f"<{name}>{body}</{name}>") for name, body in found]
    return [("spend_mirror", text)]

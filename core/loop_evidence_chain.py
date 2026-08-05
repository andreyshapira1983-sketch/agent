"""Досборка цепочки улик — вырезано из ``core/loop.py`` дословно.

Правило оператора: «ни один файл кода не длиннее 2000 строк» и «разбирай
большие файлы на компактные подключаемые модули — не дублируя и не искажая».
Четвёртый кусок раскола `core/loop.py` и второй — раскола `_run_inner`.

Улики уровня инструментов добавляются в цепочку пошагово, внутри попытки.
Три источника приходят по другим путям и досыпаются ЗДЕСЬ, одним местом,
чтобы верификатор видел одну однородную цепочку: записи долгой памяти,
кэшированные выводы инструментов из рабочей памяти прошлых ходов и дословная
запись самого диалога (issue #119).

Защита — ПОРЕКОРДНАЯ, а не вокруг цикла (MIR-061): с `try` снаружи одна
незашедшая запись бросала весь цикл, и всё, что шло за ней, исчезало из
цепочки молча — замерено 1 из 5 доехавших, — после чего верификатор судил
ответ по урезанной цепочке. Три цикла здесь обязаны не разъехаться по
гранулярности снова.

Второй метод здесь — то, что происходит с УЖЕ СОБРАННОЙ цепочкой: сенсор
преждевременного завершения (теневой, оставлен только для сверки со сменившей
его проверкой обязательств), ранжирование источников и каталогизация через
конвейер знаний. Дешёвый путь их пропускает: цепочка пуста, каталогизировать
нечего, а платить за проход — есть чем.

Границы выбрали измерением: досборке на входе три имени (`chain`,
`persistent_block`, `self`), наружу — ничего; цепочка меняется на месте,
ссылка на неё кладётся на цикл. Тела перенесены символ в символ, что пинится AST-сверкой с историей
в `tests/test_loop_evidence_chain_split.py`.

Класс подмешивается в ``AgentLoop``; состояние по-прежнему живёт на
композированном цикле, а не здесь.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.evidence import (
    ProvenanceChain,
    evidence_from_memory_record,
    evidence_from_prior_turn,
    make_evidence,
)
from core.source_ranker import SourceRankingReport, rank_chain


class AgentLoopEvidenceChain:
    """Досборка цепочки: память, рабочие артефакты, диалог.

    Члены ниже — объявления контракта хоста (``AgentLoop`` их создаёт в
    ``__init__``); присваиваний нет, поэтому во время выполнения ничего не
    создаётся и не затеняется. Тот же приём, что в ``loop_step_execution``.
    """

    if TYPE_CHECKING:  # pragma: no cover — только объявления
        log: Any
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

    def _fold_evidence_chain(
        self,
        chain: ProvenanceChain,
        *,
        persistent_block: str,
    ) -> None:
        """Досыпать в цепочку то, что пришло не через шаги плана.

        Меняет `chain` НА МЕСТЕ и кладёт её на цикл (`last_provenance`) —
        подпись `-> None` про это и говорит: новой цепочки не возникает,
        и вызывающий продолжает работать с той же самой.
        """
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
                except Exception as exc:  # наблюдательный сенсор: сбой журналируется, ход не ломается
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
        """Теневой вердикт сенсора, ранжирование источников и реестр.

        Первый элемент — НЕ решение: ключевой детектор преждевременного
        завершения оставлен только для сверки со сменившей его проверкой
        обязательств (замерен на 1/12 полноты и срабатывает на «объясни
        разницу…», потому что `разниц` — слово diff-инструмента). Вызывающий
        несёт его в событие, а не действует по нему.
        """
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
        except Exception as exc:  # наблюдательный сенсор: сбой журналируется, ход не ломается
            self._sensor_failed("premature_completion_risk", exc)

        self.log.log(
            "evidence_collected",
            {
                "count": len(chain),
                "kinds": sorted({ev.kind for ev in chain.evidences}),
                "chain": chain.to_log_payload(),
            },
        )
        source_ranking = rank_chain(chain, question=user_question)
        self.last_source_ranking = source_ranking
        self.log.log("source_ranking", source_ranking.to_log_payload())
        if cheap_path_active:
            # Cheap path: the chain is empty (no tools ran) so the knowledge
            # pipeline and source-registry build have nothing to catalog.
            # Skip them to avoid the per-turn cost the user flagged, and keep
            # the empty registry reset at the top of run().
            source_registry = self.last_source_registry
            self.log.log(
                "knowledge_pipeline_skipped",
                {"reason": "cheap_path", "chain_size": len(chain)},
            )
        else:
            knowledge_result = self.knowledge_pipeline.run(
                chain,
                ranking=source_ranking,
                source_store=self.source_registry_store if may_source_registry else None,
                remember=self._knowledge_remember_batch() if may_knowledge else None,
                auto_write_memory=(
                    self.knowledge_auto_write if may_knowledge else False
                ),
                # Unattended runs demand corroboration; a human at the REPL
                # can judge a single source themselves.
                require_verified=self._unattended_run(),
            )
            source_registry = knowledge_result.registry
            self.last_source_registry = source_registry
            self.log.log("source_registry", source_registry.to_log_payload())
            self.last_knowledge_pipeline = knowledge_result
            self.log.log("knowledge_pipeline", knowledge_result.to_log_payload())

        return _premature_keyword_fired, source_ranking, source_registry

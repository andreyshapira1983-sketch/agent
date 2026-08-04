"""Проверка черновика и сенсоры вокруг неё — вырезано из ``core/loop.py``.

Правило оператора: «ни один файл кода не длиннее 2000 строк» и «разбирай
большие файлы на компактные подключаемые модули — не дублируя и не искажая».
Пятый кусок раскола `core/loop.py` и третий — раскола `_run_inner`.

Здесь проходит ПЕРВАЯ проверка черновика и три наблюдателя вокруг неё:
расхождения подсистем (планировщик против верификатора), вектор уверенности
и поддержка уликами. Все трое — телеметрия: журналируют и ничего не решают,
поэтому сбой любого из них не имеет права уронить ход.

Мягкий отказ — суть этого участка. Упавший верификатор НЕ означает «улик не
хватило»: черновик сохраняется как есть, поднимается флаг `verifier_failure`,
и дальше по цепочке решателей он отличает «проверка сломалась» от «проверка
прошла и не подтвердила». Слить эти два случая — значит наказать ответ за
поломку инструмента, который его судил.

Дальше по коду цикла остаётся перепланирование по неразрешённым цитатам: оно
перезапускает попытку целиком и потому держится почти за всё состояние
прогона — этот кусок сюда не поехал сознательно.

Тела перенесены символ в символ, что пинится AST-сверкой с историей в
`tests/test_loop_verification_split.py`; добавки — только швы переноса,
названные там поимённо.

Класс подмешивается в ``AgentLoop``; состояние по-прежнему живёт на
композированном цикле, а не здесь.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.evidence import ProvenanceChain
from core.evidence_support import evaluate_evidence_support
from core.low_evidence_policy import is_evidence_expected
from core.models import Plan
from core.replan import ReplanTrigger

if TYPE_CHECKING:  # pragma: no cover — только для подписи
    from core.verifier_models import VerificationReport


class AgentLoopVerification:
    """Первая проверка черновика: вердикт плюс телеметрия вокруг него.

    Члены ниже — объявления контракта хоста (``AgentLoop`` их создаёт в
    ``__init__``); присваиваний нет, поэтому во время выполнения ничего не
    создаётся и не затеняется. Тот же приём, что в ``loop_step_execution``.
    """

    if TYPE_CHECKING:  # pragma: no cover — только объявления
        log: Any
        last_verification: Any
        last_provenance: Any
        last_role_context: Any
        last_source_ranking: Any

        # Объявляем ВЫЗЫВАЕМЫМИ атрибутами: заглушка-функция с пустым телом
        # читается анализаторами как «функция без return», и каждый вызов
        # ложно помечается E1111.
        _sensor_failed: Any

        # Берётся у соседней примеси: работает через MRO, но связь между
        # модулями обязана быть записана, иначе её видно только на прогоне.
        _file_read_workspace_root: Any

    def _verify_draft(
        self,
        draft_answer: str,
        *,
        chain: ProvenanceChain,
        user_question: str,
        attempt: int,
        plan: Plan,
        artifacts: dict[str, dict[str, Any]],
        failure_history: list[ReplanTrigger],
        _disagreement_shadow: list[dict[str, Any]],
    ) -> tuple[VerificationReport, bool]:
        """Вердикт по черновику и признак «верификатор упал».

        Второй элемент — НЕ «не подтвердилось», а «проверка сломалась».
        Вызывающий обязан различать их: на мягком отказе отчёт пустой, но
        черновик цел, и наказывать ответ за это нельзя.

        `_disagreement_shadow` меняется НА МЕСТЕ (теневой учёт S5), имя
        сохранено как в цикле — перенос дословный.
        """
        # Швы переноса, названные в `tests/test_loop_verification_split.py`.
        # Флаг заводился строкой выше вырезанного участка; импорты — теми же
        # локальными, что и в цикле (там они локальны, и здесь тоже: перенос
        # не место для смены момента импорта).
        verifier_failure = False
        from core.verifier import verify as _verify
        from core.verifier_models import VerificationReport as _VRSoft

        try:
            report = _verify(
                answer=draft_answer,
                chain=chain,
                user_question=user_question,
                expects_contract_headers=getattr(
                    self, "_synthesis_expects_contract_headers", True
                ),
                **self._verification_receipt_kwargs(),
            )
        except Exception as _ver_exc:
            # Soft-fail: keep draft; do not pretend "insufficient evidence".
            verifier_failure = True
            self.log.log(
                "verifier_failure",
                {
                    "error_type": type(_ver_exc).__name__,
                    "error": str(_ver_exc)[:300],
                    "draft_chars": len(draft_answer),
                    "phase": "initial",
                },
            )
            report = _VRSoft(
                total_chunks=0,
                verified_chunks=0,
                unverified_chunks=0,
                cited_but_unmatched_chunks=0,
                self_declared_chunks=0,
                structural_chunks=0,
                chunks=(),
                annotated_answer=draft_answer,
                fully_unverified=False,
                chain_was_empty=True,
                disclaimer=None,
                malformed_output=False,
            )
        self.log.log("verification", report.to_log_payload())

        # P1 — observational cross-subsystem audit. Compare planner
        # outcome (steps done/failed, artifacts produced) against
        # verifier verdict and emit a `subsystem_disagreement` event
        # for each conflicting pair. Logging only — no behaviour
        # change at this layer.
        _disagreements: list[dict] = []
        try:
            from core.subsystem_disagreement import detect_disagreements
            _disagreements = detect_disagreements(
                attempt=attempt,
                plan_steps=plan.steps,
                artifacts=artifacts,
                report=report,
                failure_history=failure_history,
            )
            for _ev in _disagreements:
                self.log.log("subsystem_disagreement", _ev)
                # Shadow accounting (operator ruling 2026-07-27): what a
                # connected S5 would have done, recorded and never acted on.
                # Severity decides the action: a full planner/verifier
                # contradiction is an escalation, the rest is a replan.
                _disagreement_shadow.append({
                    "kind": _ev.get("kind"),
                    "severity": _ev.get("severity"),
                    "attempt": _ev.get("attempt"),
                    "would_action": (
                        "escalate" if _ev.get("severity") == "high"
                        else "replan"
                    ),
                })
        except Exception as exc:  # наблюдательный сенсор: сбой журналируется, ход не ломается
            self._sensor_failed("subsystem_disagreement", exc)

        # P1/P2 — confidence vector. Decompose the scalar gate into
        # three axes (evidence / coherence / relevance) so triage
        # can target the right subsystem when something is off.
        # Logging only.
        try:
            from core.confidence_vector import compute_vector
            _cv = compute_vector(
                report=report,
                disagreements=_disagreements,
                question=user_question,
                answer=draft_answer,
            )
            self.log.log("confidence_vector", _cv.to_log_payload())
        except Exception as exc:  # наблюдательный сенсор: сбой журналируется, ход не ломается
            self._sensor_failed("confidence_vector", exc)
        if report.malformed_output:
            self.log.log(
                "output_contract_violation",
                {
                    "reason": "LLM answer contains no Output Contract section headers",
                    "total_chunks": report.total_chunks,
                    "structural_chunks": report.structural_chunks,
                },
            )
        self.last_verification = report
        self.last_provenance = chain

        # Evidence support — telemetry, never a gate (operator ruling
        # 2026-07-27). Emitted on every verified turn, including the
        # not-applicable ones: "this turn owed no evidence" is exactly the
        # case the old `low_confidence_gate` reported as a zero score, and
        # distinguishing it is the whole point of the rewrite.
        #
        # Applicability is asked with the SAME inputs the enforcing layer
        # uses further down, so observer and enforcer cannot hold opposite
        # opinions about whether evidence was owed on this turn.
        try:
            _ev_expected = is_evidence_expected(
                role=getattr(self.last_role_context, "role", ""),
                chain_was_empty=bool(
                    getattr(report, "chain_was_empty", False)
                ),
                realtime_required=bool(
                    getattr(self.last_source_ranking, "realtime_required", True)
                ),
                answer=draft_answer,
            )
            _support = evaluate_evidence_support(
                report, evidence_expected=_ev_expected
            )
            self.log.log("evidence_support", _support.to_log_payload())
        except Exception as exc:  # наблюдательный сенсор: сбой журналируется, ход не ломается
            self._sensor_failed("evidence_support", exc)

        return report, verifier_failure

    def _verification_receipt_kwargs(self) -> dict[str, Any]:
        from core.tool_receipts import ToolReceiptLedger, default_receipts_path

        log = getattr(self, "log", None)
        trace_id = str(getattr(log, "trace_id", "") or "") if log is not None else ""
        root = self._file_read_workspace_root()
        if root is None:
            return {"receipt_ledger": None, "trace_id": trace_id or None}
        return {
            "receipt_ledger": ToolReceiptLedger(default_receipts_path(root)),
            "trace_id": trace_id or None,
        }

"""Исполнение одного шага плана — вырезано из ``core/loop.py`` дословно.

Правило оператора (2026-08-03): «разбирай большие файлы на компактные
подключаемые модули — не дублируя и не искажая». Здесь живёт всё, что
касается ОДНОГО шага: политика и одобрение, вызов инструмента, разбор
результата, классификация данных, защита от инъекций, план компенсации и
параллельный запуск читающих шагов. 812 строк уехали сюда из цикла,
поведение не менялось — тела методов перенесены символ в символ, что
пинится AST-сверкой в `tests/test_loop_step_execution_split.py`.

Класс подмешивается в ``AgentLoop``; всё состояние по-прежнему живёт на
композированном цикле, а не здесь.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Any, Literal

from core.data_classifier import DataClass, SourceHint, classify
from core.injection_guard import (
    _to_text,
    annotate_suspicious,
    scan_for_injection,
    untrusted_scan_view,
)
from core.models import (
    Action,
    ApprovalRequest,
    ErrorObject,
    PlanStep,
    PolicyDecision,
    ToolCall,
    ToolResult,
)
from core.redaction import collect_pii_findings, redact_payload, scan
from core.replan import FailureType as ReplanCode
from core.replan import ReplanTrigger

# Thread-local storage for per-step replan triggers.
# _execute_step writes here instead of self._last_step_failure so that
# parallel worker threads each own an isolated slot — no shared-state race.
_step_trigger_tls: threading.local = threading.local()

# Tools that read only from the local workspace (trusted boundary).
# Injection guard is skipped for these — their output cannot be injected
# by an external adversary and false-positives degrade signal quality.
_TRUSTED_INTERNAL_TOOLS: frozenset[str] = frozenset({
    "file_read",
    "list_dir",
    "diff_file",
    "run_tests",
    "read_logs",
})


# Maps tool names to data_classifier source hints. Drives the per-tool
# default DataClass (file_read -> private, web_search -> public, …).
_TOOL_SOURCE_HINTS: dict[str, SourceHint] = {
    "file_read": "file",
    "web_search": "web",
    # web_fetch returns a fetched public page body; without this entry the
    # lookup fell back to "tool_output" -> PRIVATE (MGA-06 / CORE-09), so a
    # public page fetched via web_fetch lost its `public` classification.
    "web_fetch": "web",
}


class AgentLoopStepExecution:
    """Шаг плана: одобрение, вызов инструмента, разбор, безопасность.

    Члены ниже — объявления контракта хоста (``AgentLoop`` их создаёт в
    ``__init__``); присваиваний нет, поэтому во время выполнения ничего не
    создаётся и не затеняется. Тот же приём, что в ``loop_methods``.
    """

    if TYPE_CHECKING:  # pragma: no cover — только объявления
        log: Any
        policy: Any
        registry: Any
        memory: Any
        approval_provider: Any
        compensation_log: Any
        gateway_dry_run: Any
        gateway_path: Any
        _current_attempt: int
        # Как в `AgentLoop.__init__`: список или None до начала цикла.
        _cycle_findings: list[dict[str, Any]] | None

        # Объявляем ВЫЗЫВАЕМЫМ атрибутом: заглушка-функция с пустым телом
        # читается анализаторами как «функция без return», и каждый вызов
        # ложно помечается E1111.
        _file_read_workspace_root: Any

    # ------------------------------------------------------------------
    # Parallel step execution helpers
    # ------------------------------------------------------------------

    def _run_step_parallel(
        self, step: PlanStep
    ) -> tuple[PlanStep, dict[str, Any] | None, ReplanTrigger | None]:
        """Thread-safe wrapper: runs _execute_step and returns (step, outcome, trigger).

        Clears the thread-local trigger slot before calling _execute_step so that
        each thread starts with a clean slate. Can be submitted safely to a
        ThreadPoolExecutor worker.
        """
        _step_trigger_tls.step_trigger = None
        outcome = self._execute_step(step)
        trigger = getattr(_step_trigger_tls, "step_trigger", None)
        _step_trigger_tls.step_trigger = None  # consume
        return step, outcome, trigger

    def _step_only_reads(self, step: PlanStep) -> bool:
        """True when this step cannot change the workspace.

        Asks the tool the same question the policy gate asks — `risk_for` on
        the step's own arguments, not the tool's class-level risk, because
        `shell_exec` is read-only for `git log` and irreversible for
        `git commit`. Anything unresolvable (no tool name, tool not in the
        registry, a tool that raises) is treated as an effect: an unknown step
        must not buy concurrency.
        """
        spec = step.action_spec or {}
        tool_name = spec.get("tool_name")
        if not isinstance(tool_name, str) or not tool_name:
            return False
        try:
            tool = self.registry.get(tool_name)
        except Exception:  # noqa: BLE001 — инструмента нет в реестре: считаем НЕ read_only (безопасная сторона)
            return False
        arguments = spec.get("arguments")
        try:
            return tool.risk_for(arguments if isinstance(arguments, dict) else {}) == "read_only"
        except Exception:  # noqa: BLE001 — инструмент не умеет оценить риск: считаем НЕ read_only (безопасная сторона)
            return False

    def _execute_steps_parallel(
        self, steps: list[PlanStep]
    ) -> list[tuple[PlanStep, dict[str, Any] | None, ReplanTrigger | None]]:
        """Execute a list of plan steps, running independent steps in parallel.

        Steps whose ``preconditions`` list is empty (the common case — the
        planner currently never fills it) are all considered independent and
        run concurrently in a ThreadPoolExecutor. Steps that declare a
        precondition referencing another step in the same batch are run
        sequentially after the parallel group completes.

        Returns results in plan order: [(step, outcome, trigger), ...].
        """
        if not steps:
            return []
        if len(steps) == 1:
            return [self._run_step_parallel(steps[0])]

        # Concurrency is safe between steps that only READ. The moment one of
        # them changes the workspace, plan order stops being a formality and
        # becomes the work itself — and `preconditions`, which is what this
        # function partitions on, is never filled by the planner (see above),
        # so every step reads as independent.
        #
        # Measured on a live run: a correct six-step plan (write, write, test,
        # branch, add, commit) executed as run_tests → checkout → write → add →
        # commit → write. `git add` ran before the file existed
        # ("fatal: pathspec … did not match any files") and the commit failed
        # after it. The plan was right; the execution order threw it away.
        #
        # So: any effect in the batch, and the whole batch runs in plan order.
        # Not just the effect steps — a read can depend on a write
        # (`run_tests` after `file_write`) exactly as a write can depend on a
        # read, and the ordering between the two kinds is the part that
        # matters. Pure-research plans, which is what most questions produce,
        # keep the parallel path untouched.
        if any(not self._step_only_reads(step) for step in steps):
            # Sorted, not merely iterated: the concurrent path below sorts its
            # RESULTS back into plan order, so this one must not depend on the
            # caller happening to pass a sorted slice — the guarantee is the
            # whole point of the branch.
            return [
                self._run_step_parallel(step)
                for step in sorted(steps, key=lambda s: s.order)
            ]

        # Partition: parallel = no intra-batch dependencies; sequential = rest.
        step_ids = {s.id for s in steps}
        parallel = [s for s in steps if not any(pc in step_ids for pc in s.preconditions)]
        sequential = [s for s in steps if any(pc in step_ids for pc in s.preconditions)]

        results: list[tuple[PlanStep, dict[str, Any] | None, ReplanTrigger | None]] = []

        if len(parallel) > 1:
            max_workers = min(len(parallel), 4)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(self._run_step_parallel, step): step
                    for step in parallel
                }
                results.extend(
                    future.result() for future in as_completed(futures)
                )
        else:
            results.extend(self._run_step_parallel(step) for step in parallel)

        # Sequential steps follow in plan order.
        results.extend(self._run_step_parallel(step) for step in sequential)

        # Re-sort to plan order so callers process artifacts in a stable sequence.
        order_map = {s.id: i for i, s in enumerate(steps)}
        results.sort(key=lambda r: order_map.get(r[0].id, 9999))
        return results

    def _execute_step(self, step: PlanStep) -> dict[str, Any] | None:
        """Run a single PlanStep through Act -> Policy -> Tool -> Verify.

        Working Memory short-circuit: if (tool, arguments) is already in the
        artifact cache, we skip Policy + Tool + Verify and reuse the cached
        output. Read-only tools are deterministic enough that this is safe
        within a single session.

        Returns artifact dict on success, None on hard failure. On failure
        the method also writes a `ReplanTrigger` to `_step_trigger_tls.step_trigger`
        so the parent loop can decide whether to re-plan. Using thread-local
        storage makes this safe to call from a ThreadPoolExecutor worker.
        """
        # Clear the scratch slot so the caller never sees a stale trigger
        # from a previous step within the same attempt.
        _step_trigger_tls.step_trigger = None

        tool_name = step.action_spec.get("tool_name")
        source_label = step.action_spec.get("source_label") or f"step:{step.id}"
        arguments = step.action_spec.get("arguments", {})

        # MAST FM-1.3 — track (tool, args) repetition across the whole run.
        # Logged only the first time the count crosses the threshold so the
        # event remains a single signal, not a per-replan stream.
        _tracker = getattr(self, "_step_repetition", None)
        if _tracker is not None and tool_name:
            _rep = _tracker.observe(tool_name, arguments)
            if _rep is not None:
                self.log.log("step_repetition_detected", _rep)

        # Memory cache short-circuit
        if self.memory is not None and tool_name:
            cached = self.memory.cache_lookup(tool_name, arguments)
            if cached is not None:
                self.log.log(
                    "memory_cache_hit",
                    {
                        "tool": tool_name,
                        "arguments": arguments,
                        "label": cached["label"],
                        "stored_at_turn": cached["turn_index"],
                    },
                )
                return {
                    "label": cached["label"],
                    "tool": tool_name,
                    "arguments": arguments,
                    "output": cached["output"],
                    "issues": ["served from working-memory cache"],
                }

        action = Action(
            step_id=step.id,
            type=step.action_spec["type"],
            tool_name=tool_name,
            parameters=arguments,
            side_effects="read" if tool_name else "none",
        )
        self.log.log("act", action, source_label=source_label)

        from core.actuation_gateway import (
            ActuationGateway,
            is_effectful_tool,
            simulate_output,
        )

        if is_effectful_tool(tool_name or "", arguments, self.registry):
            from core.budget_kill_switch import BudgetKillSwitch, default_path

            ws = self._file_read_workspace_root()
            kill_switch = getattr(self, "gateway_kill_switch", None)
            if kill_switch is None and ws is not None:
                kill_switch = BudgetKillSwitch(path=default_path(ws))
            gateway = ActuationGateway(
                self.policy,
                path=self.gateway_path,
                dry_run=self.gateway_dry_run,
                kill_switch=kill_switch,
                budget_snapshot=getattr(self, "gateway_budget_snapshot", None),
                readiness_blockers=getattr(self, "gateway_readiness_blockers", ()),
                check_readiness=bool(getattr(self, "gateway_check_readiness", False)),
            )
            gw = gateway.evaluate(action, registry=self.registry)
            self.log.log("gateway_decision", gw.to_log_payload())
            # G4: durable gateway decision receipt (kind="gateway"). Never raises;
            # observation only — does not change the verdict handling below.
            from core.tool_receipts import (  # local import: avoid cycles
                receipt_context as _gw_receipt_context,
            )
            from core.tool_receipts import (
                record_gateway_receipt as _record_gateway_receipt,
            )

            with _gw_receipt_context(
                trace_id=self.log.trace_id,
                path=self.gateway_path,
                workspace=self._file_read_workspace_root(),
            ):
                _record_gateway_receipt(gw)
            if gw.outcome == "simulate":
                return {
                    "label": source_label,
                    "tool": tool_name,
                    "arguments": arguments,
                    "output": simulate_output(tool_name or "", arguments),
                    "issues": ["gateway simulate — effect not executed"],
                    "data_class": "internal",
                }
            if gw.outcome == "deny":
                err = ErrorObject(
                    source="gateway",
                    code="policy_blocked",
                    message=f"Action blocked: {', '.join(gw.reasons)}",
                    severity="error",
                    recoverable=False,
                    context={
                        "action_id": action.id,
                        "decision": "deny",
                        "source_label": source_label,
                    },
                )
                self.log.log("error", err)
                _step_trigger_tls.step_trigger = ReplanTrigger(
                    code="policy_blocked",
                    step_id=step.id,
                    tool_name=tool_name,
                    arguments=arguments,
                    reason=f"Gateway denied: {', '.join(gw.reasons) or 'unknown'}",
                    attempt=self._current_attempt,
                )
                return None
            if gw.outcome == "block":
                err = ErrorObject(
                    source="gateway",
                    code="gateway_blocked",
                    message=f"Hard stop: {', '.join(gw.reasons)}",
                    severity="error",
                    recoverable=False,
                    context={
                        "action_id": action.id,
                        "decision": "block",
                        "source_label": source_label,
                    },
                )
                self.log.log("error", err)
                _step_trigger_tls.step_trigger = ReplanTrigger(
                    code="policy_blocked",
                    step_id=step.id,
                    tool_name=tool_name,
                    arguments=arguments,
                    reason=f"Gateway blocked: {', '.join(gw.reasons) or 'unknown'}",
                    attempt=self._current_attempt,
                )
                return None
            decision = gw.policy
            assert decision is not None
            self.log.log("policy", decision)
        else:
            # Policy Gate — pre-execution checkpoint (non-effectful tools)
            decision = self.policy.check(action)
            self.log.log("policy", decision)

            if decision.decision == "deny":
                err = ErrorObject(
                    source="policy",
                    code="policy_blocked",
                    message=f"Action blocked: {', '.join(decision.reasons)}",
                    severity="error",
                    recoverable=False,
                    context={
                        "action_id": action.id,
                        "decision": "deny",
                        "source_label": source_label,
                    },
                )
                self.log.log("error", err)
                _step_trigger_tls.step_trigger = ReplanTrigger(
                    code="policy_blocked",
                    step_id=step.id,
                    tool_name=tool_name,
                    arguments=arguments,
                    reason=f"Policy gate denied: {', '.join(decision.reasons) or 'unknown'}",
                    attempt=self._current_attempt,
                )
                return None

        if decision.decision == "escalate":
            # PolicyGate flagged a risky action. Bring in the human via the
            # ApprovalProvider; if no provider is wired, the safe default
            # is to refuse — exactly the pre-MVP-6 behaviour.
            verdict = self._request_approval(
                action=action,
                step=step,
                source_label=source_label,
                policy_decision=decision,
                arguments=arguments,
            )
            if verdict != "approve":
                # Map the approval-side verdict back to a replan code
                # so the planner can pick a safer alternative (e.g.
                # swap an irreversible action for a read-only one).
                code: ReplanCode = (
                    "approval_deny" if verdict == "deny"
                    else "approval_abort" if verdict == "abort"
                    else "approval_unavailable"
                )
                # PolicyGate would have denied an unknown tool before we
                # got here, so the registry lookup is safe.
                risk = (
                    self.registry.get(tool_name).risk if tool_name else "unknown"
                )
                _step_trigger_tls.step_trigger = ReplanTrigger(
                    code=code,
                    step_id=step.id,
                    tool_name=tool_name,
                    arguments=arguments,
                    reason=f"Approval gate returned '{verdict}' for risk={risk}",
                    attempt=self._current_attempt,
                )
                return None

        if action.type != "tool_call":
            _step_trigger_tls.step_trigger = ReplanTrigger(
                code="unknown",
                step_id=step.id,
                tool_name=tool_name,
                arguments=arguments,
                reason=f"Unsupported action type: {action.type}",
                attempt=self._current_attempt,
            )
            return None  # not used in MVP-2

        # Tool execution
        result = self._call_tool(action)
        if result.status != "success":
            raw_error = result.error or "tool execution failed"
            # Detect file-absence specifically: FileNotFoundError (or
            # IsADirectoryError) means the file simply does not exist on
            # disk. This is a hard stop — the planner must NOT retry the
            # same path, search the web, or recreate the file from
            # general knowledge. Use a dedicated FailureType so the
            # replan policy communicates this clearly.
            _file_absent_prefixes = ("FileNotFoundError:", "IsADirectoryError:")
            if any(raw_error.startswith(p) for p in _file_absent_prefixes):
                replan_code: ReplanCode = "file_not_found"
            else:
                replan_code = "tool_error"
            err = ErrorObject(
                source=action.tool_name or "tool",
                code=replan_code,
                message=raw_error,
                severity="error",
                recoverable=False,
                context={"tool_call_id": result.tool_call_id, "source_label": source_label},
            )
            self.log.log("error", err)
            _step_trigger_tls.step_trigger = ReplanTrigger(
                code=replan_code,
                step_id=step.id,
                tool_name=tool_name,
                arguments=arguments,
                reason=raw_error,
                attempt=self._current_attempt,
            )
            return None

        # Tool Result Validation (delegates to the tool itself)
        tool = self.registry.get(action.tool_name)  # type: ignore[arg-type]
        is_ok, issues = tool.validate_output(result.output)
        self.log.log(
            "verify",
            {
                "step_id": step.id,
                "ok": is_ok,
                "issues": issues,
                "source_label": source_label,
            },
        )
        if not is_ok:
            err = ErrorObject(
                source="verifier",
                code="verify_failed",
                message=f"Tool Result Validation failed: {', '.join(issues) or 'unspecified'}",
                severity="error",
                recoverable=True,
                context={"step_id": step.id, "source_label": source_label},
            )
            self.log.log("error", err)
            _step_trigger_tls.step_trigger = ReplanTrigger(
                code="verify_failed",
                step_id=step.id,
                tool_name=tool_name,
                arguments=arguments,
                reason=f"Validation rejected output: {'; '.join(issues) or 'no detail'}",
                attempt=self._current_attempt,
            )
            return None

        # --- MVP-12: structured-failure reclassification ----------------------
        # Two outcomes pass validate_output but still need a re-plan with a
        # specific advice: empty web_search results and shell_exec timeouts.
        # Detecting them HERE (not inside the tools) keeps the tool contracts
        # stable while letting the ReplanPolicy advise the planner per case.
        if action.tool_name == "web_search" and isinstance(result.output, list) and len(result.output) == 0:
            self.log.log(
                "error",
                ErrorObject(
                    source="web_search",
                    code="web_empty",
                    message="web_search returned zero hits — query needs reformulation",
                    severity="error",
                    recoverable=True,
                    context={"step_id": step.id, "source_label": source_label},
                ),
            )
            _step_trigger_tls.step_trigger = ReplanTrigger(
                code="web_empty",
                step_id=step.id,
                tool_name=tool_name,
                arguments=arguments,
                reason="web_search returned 0 results; reformulate the query",
                attempt=self._current_attempt,
            )
            return None
        if action.tool_name == "shell_exec" and isinstance(result.output, dict) and result.output.get("timed_out"):
            self.log.log(
                "error",
                ErrorObject(
                    source="shell_exec",
                    code="timeout",
                    message="shell_exec hit its timeout — output truncated, exit_code unknown",
                    severity="error",
                    recoverable=True,
                    context={"step_id": step.id, "source_label": source_label},
                ),
            )
            _step_trigger_tls.step_trigger = ReplanTrigger(
                code="timeout",
                step_id=step.id,
                tool_name=tool_name,
                arguments=arguments,
                reason="shell_exec timed out; reduce scope or pick a faster path",
                attempt=self._current_attempt,
            )
            return None

        # --- Safety pipeline (§7 / MVP-7) ---------------------------------
        # 1. Classify the tool output.
        # 2. Emit secret_detected when the classifier returns SECRET.
        # 3. Deep-redact the output BEFORE it reaches artifacts, memory
        #    cache, or the synthesizer prompt. From this point on the loop
        #    only handles redacted text.
        #
        # Outer guard: classification / redaction helpers should never raise,
        # but if they do (e.g. a corrupted output string), we capture the
        # exception as an "unknown" replan trigger so the plan can retry
        # rather than crashing the entire cycle.
        try:
            source_hint: SourceHint = _TOOL_SOURCE_HINTS.get(
                action.tool_name or "", "tool_output"
            )
            flat_output = _to_text(result.output)
            # Trusted internal tools (our own logs, files, diffs, test output)
            # live inside the trusted boundary. A bare mention of a credential
            # word like "api_key" in that content is not a leaked secret and
            # must not quarantine the agent's own evidence — so the soft
            # keyword layer is disabled for them. Real credential SHAPES
            # (regex spans) are still detected and classified SECRET.
            keyword_secrets = (action.tool_name or "") not in _TRUSTED_INTERNAL_TOOLS
            cls_result = classify(
                flat_output, source=source_hint, keyword_secrets=keyword_secrets
            )
            self.log.log(
                "data_classified",
                {
                    "label": source_label,
                    "tool": action.tool_name,
                    "class": cls_result.cls.value,
                    "source": cls_result.source,
                    "reasons": cls_result.reasons,
                },
            )

            # --- Injection Guard (§2 Adversarial Defence) ------------------
            # Scan tool output for indirect prompt-injection patterns BEFORE
            # the content reaches the synthesizer prompt.  Blocked content is
            # dropped and triggers a replan; suspicious content is annotated
            # and passed through with a trust warning.
            # Internal workspace tools (file_read, list_dir, …) are exempt:
            # their content originates inside the trusted boundary and
            # scanning them produces false-positives with no security benefit.
            if action.tool_name in _TRUSTED_INTERNAL_TOOLS:
                inj = None
            else:
                # Scan ONLY the untrusted payload, not framework-generated
                # envelope metadata (argv, compensation-plan descriptions,
                # timing). Scanning the whole envelope tripped override
                # patterns on our own text (e.g. shell_exec's
                # "read-only command 'where'; …") -> false-positive
                # injection_suspicious. See untrusted_scan_view().
                inj = scan_for_injection(
                    untrusted_scan_view(action.tool_name, result.output)
                )
            if inj is not None and inj.verdict != "clean":
                self.log.log(
                    "injection_" + inj.verdict,
                    {
                        "label": source_label,
                        "tool": action.tool_name,
                        **inj.to_log_payload(),
                    },
                )
            if inj is not None and inj.is_blocked:
                _step_trigger_tls.step_trigger = ReplanTrigger(
                    code="injection_blocked",
                    step_id=step.id,
                    tool_name=tool_name,
                    arguments=arguments,
                    reason=(
                        "Tool output blocked by injection guard "
                        f"({len(inj.findings)} finding(s)). "
                        "Try a different source or query."
                    ),
                    attempt=self._current_attempt,
                )
                return None
            if inj is not None and inj.verdict == "suspicious":
                # Wrap the output with a trust-warning annotation so the
                # synthesizer knows the content may be adversarial.
                annotated = annotate_suspicious(flat_output, source_label)
                result = result.model_copy(update={"output": annotated})
                flat_output = annotated
            # ---------------------------------------------------------------

            if cls_result.cls == DataClass.SECRET:
                findings = scan(flat_output)
                kinds = sorted({f.kind for f in findings})
                self.log.log(
                    "secret_detected",
                    {
                        "label": source_label,
                        "tool": action.tool_name,
                        "kinds": kinds,
                        "count": len(findings),
                        "surface": "tool_output",
                    },
                )
                if self._cycle_findings is not None:
                    self._cycle_findings.append(
                        {"label": source_label, "kinds": kinds, "count": len(findings)}
                    )

                # Redact SECRET output before it reaches the planner,
                # synthesizer, or memory cache.
                safe_output = redact_payload(result.output)

                if self.memory is not None and action.tool_name:
                    self.memory.cache_store(
                        tool_name=action.tool_name,
                        arguments=arguments,
                        output=safe_output,
                        label=source_label,
                    )

                return {
                    "label": source_label,
                    "tool": action.tool_name,
                    "arguments": action.parameters,
                    "output": safe_output,
                    "issues": issues,
                    "data_class": cls_result.cls.value,
                }
            if cls_result.cls == DataClass.SENSITIVE:
                findings = collect_pii_findings(flat_output)
                kinds = sorted({f"pii-{f.kind}" for f in findings})
                self.log.log(
                    "sensitive_detected",
                    {
                        "label": source_label,
                        "tool": action.tool_name,
                        "kinds": kinds,
                        "count": len(findings),
                        "surface": "tool_output",
                    },
                )
                if self._cycle_findings is not None:
                    self._cycle_findings.append(
                        {"label": source_label, "kinds": kinds, "count": len(findings)}
                    )

                safe_output = redact_payload(result.output)

                # Memory write — cache the REDACTED artifact for future turns
                if self.memory is not None and action.tool_name:
                    self.memory.cache_store(
                        tool_name=action.tool_name,
                        arguments=arguments,
                        output=safe_output,
                        label=source_label,
                    )

                return {
                    "label": source_label,
                    "tool": action.tool_name,
                    # MVP-14.1: surfaced so the evidence factory can build a
                    # typed `Evidence` (source_id often depends on an argument
                    # like `path` or `url`).
                    "arguments": action.parameters,
                    "output": safe_output,
                    "issues": issues,
                    "data_class": cls_result.cls.value,
                }

            # PUBLIC / INTERNAL / SECRET-flagged data: cache original output
            # and return it to the planner. For SECRET data the loop already
            # emitted a `secret_detected` log event above; the output is
            # returned as-is so the planner can still act on it.
            if self.memory is not None and action.tool_name:
                self.memory.cache_store(
                    tool_name=action.tool_name,
                    arguments=arguments,
                    output=result.output,
                    label=source_label,
                )

            return {
                "label": source_label,
                "tool": action.tool_name,
                "arguments": action.parameters,
                "output": result.output,
                "issues": issues,
                "data_class": cls_result.cls.value,
            }

        except Exception as exc:  # noqa: BLE001
            # Last-resort guard: if any postprocessing step (classify,
            # redact, cache_store) raises unexpectedly, capture it as an
            # "unknown" replan trigger rather than crashing the loop.
            self.log.log(
                "error",
                ErrorObject(
                    source="loop",
                    code="unknown",
                    message=f"Postprocessing error in _execute_step: {type(exc).__name__}: {exc}",
                    severity="error",
                    recoverable=True,
                    context={"step_id": step.id, "source_label": source_label},
                ),
            )
            _step_trigger_tls.step_trigger = ReplanTrigger(
                code="unknown",
                step_id=step.id,
                tool_name=tool_name,
                arguments=arguments,
                reason=f"Postprocessing error: {type(exc).__name__}: {exc}",
                attempt=self._current_attempt,
            )
            return None

    def _call_tool(self, action: Action) -> ToolResult:
        assert action.tool_name is not None
        tool = self.registry.get(action.tool_name)
        call = ToolCall(
            action_id=action.id,
            tool_name=action.tool_name,
            arguments=action.parameters,
        )
        self.log.log("tool_call", call)
        from core.tool_receipts import receipt_context

        workspace = self._file_read_workspace_root()
        with receipt_context(
            trace_id=self.log.trace_id,
            path="repl",
            workspace=workspace,
        ):
            result = tool.invoke(call)
        self.log.log("tool_result", result, status=result.status, latency_ms=result.latency_ms)
        # MVP-11 Compensation capture: a successful tool call may carry a
        # `compensation_plan` block in its structured output. The agent
        # owns the plan registry from here on — `rollback()` reads from
        # this list, in LIFO order, and applies the plan inside the
        # workspace root sandbox.
        self._capture_compensation_plan(result)
        return result

    def _capture_compensation_plan(self, result: ToolResult) -> None:
        """Extract + register a `compensation_plan` from tool output, if any.

        Only success results are inspected. The plan is logged as a
        `compensation_registered` event so an audit can match every
        applied rollback to the tool call that produced it.
        """
        if result.status != "success" or not isinstance(result.output, dict):
            return
        raw = result.output.get("compensation_plan")
        if not isinstance(raw, dict):
            return
        # Skip noop plans — they pollute :rollback semantics and the
        # audit log. The plan is still IN the tool_result output, so
        # an analyst can see read-only commands also produced one.
        actions = raw.get("actions") or []
        if all(a.get("kind") == "noop" for a in actions):
            return
        from core.compensation import CompensationPlan  # avoid import cycle

        plan = CompensationPlan.from_dict(raw)
        self.compensation_log.append(plan)
        self.log.log(
            "compensation_registered",
            {
                "plan_id": plan.id,
                "tool_name": plan.tool_name,
                "description": plan.description,
                "action_count": len(plan.actions),
                "tool_call_id": result.tool_call_id,
            },
        )
    # ---------- approval ----------

    def _request_approval(
        self,
        action: Action,
        step: PlanStep,
        source_label: str,
        policy_decision: PolicyDecision,
        arguments: dict[str, Any],
    ) -> Literal["approve", "deny", "abort", "unavailable"]:
        """Bridge between the PolicyGate's `escalate` verdict and the human.

        Emits two trace events — `approval_request` then `approval_decision`
        — and turns the decision into a control-flow signal for the loop:
          - "approve" -> caller continues with the tool call
          - anything else -> caller bails out, no tool_call is logged
        """
        if self.approval_provider is None:
            err = ErrorObject(
                source="approval",
                code="approval_unavailable",
                message=(
                    "Action escalated but no approval provider is configured. "
                    f"Reasons: {', '.join(policy_decision.reasons)}"
                ),
                severity="error",
                recoverable=False,
                context={
                    "action_id": action.id,
                    "policy_decision_id": policy_decision.id,
                    "source_label": source_label,
                },
            )
            self.log.log("error", err)
            return "unavailable"

        # Tool must exist — PolicyGate would have returned deny otherwise.
        tool = self.registry.get(action.tool_name)  # type: ignore[arg-type]
        # Mirror the PolicyGate: ask the tool for its argument-aware
        # risk so the approval prompt reports what the gate actually
        # decided on, not the static class fallback.
        effective_risk = tool.risk_for(arguments or {})
        request = ApprovalRequest(
            action_id=action.id,
            step_id=step.id,
            tool_name=action.tool_name,
            arguments=arguments,
            risk=effective_risk,
            reasons=list(policy_decision.reasons),
            summary=(
                f"Tool '{action.tool_name}' wants to execute "
                f"with risk={effective_risk}."
            ),
            policy_decision_id=policy_decision.id,
        )
        self.log.log("approval_request", request)

        decision = self.approval_provider.request(request)
        self.log.log("approval_decision", decision)

        if decision.decision == "approve":
            return "approve"

        err = ErrorObject(
            source="approval",
            code=f"approval_{decision.decision}",  # approval_deny | approval_abort
            message=(
                f"Approval not granted ({decision.decision}): "
                f"{'; '.join(decision.reasons) or 'no reason given'}"
            ),
            severity="error",
            recoverable=False,
            context={
                "action_id": action.id,
                "approval_request_id": request.id,
                "approval_decision_id": decision.id,
                "responder": decision.responder,
                "source_label": source_label,
            },
        )
        self.log.log("error", err)
        return decision.decision

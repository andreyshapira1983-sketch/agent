"""Execution of one plan step (policy and approval, tool call, result parsing, data classification, injection guard, compensation plan, parallel read-only steps); mixed into ``AgentLoop``, state lives on the composed loop."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from typing import TYPE_CHECKING, Any, Literal

from core.data_classifier import DataClass, SourceHint, classify
from core.failure_cards import failed_output_reason
from core.injection_guard import (
    _to_text,
    annotate_suspicious,
    annotate_suspicious_output,
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
from core.repo_provenance import block_may_be_annotated
from core.step_references import (
    UnresolvedStepReference,
    has_step_reference,
    reject_content_reference,
    resolve_step_references,
)
from core.step_sanitizer import fit_resolved_arguments, resolved_url_refusal

# Thread-local slot for per-step replan triggers: parallel workers each own an isolated slot (no shared-state race).
_step_trigger_tls: threading.local = threading.local()

# Tools that read only the local workspace (trusted boundary): the injection guard is skipped for them.
_TRUSTED_INTERNAL_TOOLS: frozenset[str] = frozenset({
    "file_read",
    "list_dir",
    "find_in_files",
    "diff_file",
    "run_tests",
    "read_logs",
})

# Exempt from the INJECTION scan (narrower than the secret-keyword question above): the workspace is NOT a trusted boundary,
# ingest-web, ingest-rss, ingest-project, clones and downloads put outside content into it. `file_read`, `diff_file`, `list_dir`
# and `run_tests` are therefore scanned. `read_logs` stays exempt ON PURPOSE: the guard preserved its excerpts as evidence,
# and the fix belongs on the write side. The rest return framework-shaped output (scanning it tripped false positives, see untrusted_scan_view).
_INJECTION_SCAN_EXEMPT: frozenset[str] = frozenset({
    "read_logs",
})


# Maps tool names to data_classifier source hints. Drives the per-tool
# default DataClass (file_read -> private, web_search -> public, …).
_TOOL_SOURCE_HINTS: dict[str, SourceHint] = {
    "file_read": "file",
    "web_search": "web",
    # web_fetch returns a public page body; without this entry the lookup fell back to "tool_output" -> PRIVATE.
    "web_fetch": "web",
}


class AgentLoopStepExecution:
    """A plan step: approval, tool call, parsing, safety.

    Members below only declare the host contract (``AgentLoop`` creates them in ``__init__``), so nothing is created or shadowed at runtime.
    """

    if TYPE_CHECKING:  # pragma: no cover — declarations only
        log: Any
        policy: Any
        registry: Any
        memory: Any
        approval_provider: Any
        compensation_log: Any
        gateway_dry_run: Any
        gateway_path: Any
        _current_attempt: int
        # As in `AgentLoop.__init__`: a list, or None before the cycle starts.
        _cycle_findings: list[dict[str, Any]] | None

        # Declared as a CALLABLE attribute: an empty-body stub function is read by analyzers as 'no return' (E1111).
        _file_read_workspace_root: Any


    def _run_step_parallel(
        self, step: PlanStep
    ) -> tuple[PlanStep, dict[str, Any] | None, ReplanTrigger | None]:
        """Thread-safe wrapper: runs _execute_step and returns (step, outcome, trigger), clearing the thread-local trigger slot first."""
        _step_trigger_tls.step_trigger = None
        outcome = self._execute_step(step)
        trigger = getattr(_step_trigger_tls, "step_trigger", None)
        _step_trigger_tls.step_trigger = None  # consume
        return step, outcome, trigger

    def _step_only_reads(self, step: PlanStep) -> bool:
        """True when this step cannot change the workspace.

        Asks `risk_for` on the step's own arguments, not the tool's class-level risk (`shell_exec` is read-only for `git log`,
        irreversible for `git commit`). Anything unresolvable counts as an effect: an unknown step must not buy concurrency.
        """
        spec = step.action_spec or {}
        tool_name = spec.get("tool_name")
        if not isinstance(tool_name, str) or not tool_name:
            return False
        try:
            tool = self.registry.get(tool_name)
        except Exception as exc:  # noqa: BLE001 — treat as NOT read_only (safe side)
            self._risk_probe_failed(tool_name, "unknown_tool", exc)
            return False
        arguments = spec.get("arguments")
        try:
            return tool.risk_for(arguments if isinstance(arguments, dict) else {}) == "read_only"
        except Exception as exc:  # noqa: BLE001 — treat as NOT read_only (safe side)
            self._risk_probe_failed(tool_name, "risk_for_raised", exc)
            return False

    def _blocked_output_or_replan(
        self, inj: Any, step: PlanStep, action: Action, tool_name: Any,
        arguments: Any, source_label: str, flat_output: str,
    ) -> str | None:
        """Flagged output if the document is OUR OWN, otherwise replan and None.

        The guard verdict stays; only the consequence changes, and only for a source recorded in the repository history.
        """
        if block_may_be_annotated(
            action.tool_name, source_label, self._file_read_workspace_root()
        ):
            self.log.log(
                "injection_blocked_downgraded",
                {
                    "label": source_label,
                    "tool": action.tool_name,
                    "findings": len(inj.findings),
                    "reason": "source is committed to this repository",
                },
            )
            return annotate_suspicious(flat_output, source_label)
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

    def _risk_probe_failed(self, tool_name: str, reason: str, exc: BaseException) -> None:
        """Fail safe AND say so: a missing tool or a raising `risk_for` costs concurrency for the whole batch and must be visible.

        A step that genuinely writes stays silent on purpose: an event per effect step would be a stream, not a signal.
        """
        try:
            self.log.log(
                "step_risk_probe_failed",
                {
                    "tool": tool_name,
                    "reason": reason,
                    "exception_type": type(exc).__name__,
                    "effect": "step treated as writing; batch runs sequentially",
                },
            )
        except Exception:  # noqa: BLE001, S110 — last line of defence around journaling
            pass  # nosec B110 — safe output matters more than its record

    def _execute_steps_parallel(
        self, steps: list[PlanStep]
    ) -> list[tuple[PlanStep, dict[str, Any] | None, ReplanTrigger | None]]:
        """Execute plan steps, running independent ones in parallel.

        Empty ``preconditions`` means independent; steps depending on another step in the batch run sequentially afterwards.
        Returns results in plan order: [(step, outcome, trigger), ...].
        """
        if not steps:
            return []
        # A single step takes the same path as a batch (text composition and references).
        # Everything the plan calls a step (by id and by number): lets the resolver tell a reference from prose that mentions its shape.
        plan_steps = frozenset({s.id for s in steps} | {str(s.order) for s in steps})

        # Concurrency is safe only between steps that READ. With any effect in the batch, the whole batch runs in plan order
        # (a read can depend on a write and vice versa); `preconditions`, which this function partitions on, is never filled by the planner.
        # Pure-research plans keep the parallel path.
        if any(not self._step_only_reads(step) for step in steps):
            # Sorted, not merely iterated: this path must not depend on the caller passing a sorted slice.
            # References are resolved HERE too, otherwise `{{step:N.output}}` of file-writing plans stays unfilled.
            ordered: list[tuple[PlanStep, dict[str, Any] | None, ReplanTrigger | None]] = []
            for step in sorted(steps, key=lambda s: s.order):
                ordered.append(self._run_step_after_references(step, ordered, plan_steps))
            return ordered

        # Partition: parallel steps have no internal dependencies. ANY {{step:...output}} reference makes a step dependent, even one to a missing step
        # (which must reach the resolver and fail with a named reason instead of running raw).
        step_ids = {s.id for s in steps}

        def _depends_on_batch(step: PlanStep) -> bool:
            if any(pc in step_ids for pc in step.preconditions):
                return True
            return has_step_reference(step.action_spec.get("arguments", {}))

        parallel = [s for s in steps if not _depends_on_batch(s)]
        sequential = [s for s in steps if _depends_on_batch(s)]

        results: list[tuple[PlanStep, dict[str, Any] | None, ReplanTrigger | None]] = []

        if len(parallel) > 1:
            max_workers = min(len(parallel), 4)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Each worker carries THIS run's context: blocks, dry-run, ids.
                futures = {
                    executor.submit(copy_context().run, self._run_step_parallel, step): step
                    for step in parallel
                }
                results.extend(
                    future.result() for future in as_completed(futures)
                )
        else:
            results.extend(self._run_step_parallel(step) for step in parallel)

        # Sequential steps run in plan order, and only here are references resolved: the source step's output is already MEASURED.
        for step in sequential:
            results.append(self._run_step_after_references(step, results, plan_steps))

        # Re-sort to plan order so callers process artifacts in a stable sequence.
        order_map = {s.id: i for i, s in enumerate(steps)}
        results.sort(key=lambda r: order_map.get(r[0].id, 9999))
        return results

    def _run_step_after_references(
        self,
        step: PlanStep,
        done: list[tuple[PlanStep, dict[str, Any] | None, ReplanTrigger | None]],
        plan_steps: frozenset[str] | None = None,
    ) -> tuple[PlanStep, dict[str, Any] | None, ReplanTrigger | None]:
        """Resolve the step's references against already measured output, and only then execute.

        A step whose reference found no source is NOT executed; the failure goes to the planner as a trigger with a named reason.
        """
        trigger = self._resolve_references_in(step, done, plan_steps)
        if trigger is not None:
            return step, None, trigger
        trigger = self._compose_write_content(step, done)
        if trigger is not None:
            return step, None, trigger
        result = self._run_step_parallel(step)
        from core.write_at_execution import remember_read
        remember_read(self, result[0], result[1])
        return result

    def _compose_write_content(self, step: PlanStep, done: list[Any]) -> ReplanTrigger | None:
        """Compose the write content from the step's task, using this batch's outputs."""
        arguments = (step.action_spec or {}).get("arguments") or {}
        if "content" in arguments or not arguments.get("write_instruction"):
            return None
        from core.write_at_execution import compose_content

        try:
            arguments["content"] = compose_content(self, step, done)
        except Exception as exc:  # noqa: BLE001 — composition failed: step is not executed
            step.status = "failed"
            self.log.log("write_compose_failed", {
                "step": getattr(step, "order", None), "path": arguments.get("path"),
                "error": f"{type(exc).__name__}: {exc}",
            })
            return ReplanTrigger(
                code="tool_error", step_id=step.id, tool_name="file_write",
                arguments={"path": arguments.get("path")},
                reason=f"текст записи не собрался: {type(exc).__name__}: {exc}",
                attempt=0)
        arguments.pop("write_instruction", None)
        self.log.log("write_composed_at_execution", {
            "step": getattr(step, "order", None), "path": arguments.get("path"),
            "chars": len(arguments["content"]),
        })
        return None

    def _resolve_references_in(
        self,
        step: PlanStep,
        done: list[tuple[PlanStep, dict[str, Any] | None, ReplanTrigger | None]],
        plan_steps: frozenset[str] | None = None,
    ) -> ReplanTrigger | None:
        """Substitute the outputs of already executed steps into the step's arguments.

        No silent fallback: if a source gave no result, the reference stays unresolved and the step is marked failed with a named reason and NOT executed.
        The substituted value is then fitted to the sanitizer's trimming rules (`fit_resolved_arguments`).
        Returns the failure trigger, or None if the step may run.
        """
        arguments = step.action_spec.get("arguments", {})
        if not has_step_reference(arguments):
            return None
        outputs: dict[str, Any] = {}
        sources: dict[str, tuple[str, str]] = {}
        for done_step, artifact, _trigger in done:
            spec = done_step.action_spec
            sources[done_step.id] = sources[str(done_step.order)] = (
                str(spec.get("tool_name") or ""), str(spec.get("arguments", {}).get("path") or ""))
            if artifact is None:
                continue
            output = artifact.get("output")
            outputs[done_step.id] = output
            outputs[str(done_step.order)] = output
        tool_name = step.action_spec.get("tool_name")
        try:
            reject_content_reference(tool_name, arguments, sources, outputs)
            resolved = resolve_step_references(
                arguments, outputs, plan_steps=plan_steps,
            )
            refusal = resolved_url_refusal(tool_name, resolved)
            if refusal:
                raise UnresolvedStepReference(  # noqa: TRY301 — same handler as an unresolved reference
                    f"after substitution the {refusal}; the same locks apply to an "
                    f"address taken from a step as to one written in the plan")
        except UnresolvedStepReference as exc:
            self.log.log("step_reference_unresolved", {
                "step_id": step.id,
                "tool": tool_name,
                "reason": str(exc),
                "known_steps": sorted(outputs),
            })
            step.action_spec["arguments"] = arguments
            step.status = "failed"
            return ReplanTrigger(
                code="tool_error",
                step_id=step.id,
                tool_name=tool_name,
                arguments=arguments,
                reason=f"Step not executed: {exc}",
                attempt=self._current_attempt,
            )
        resolved, fitted = fit_resolved_arguments(tool_name, resolved)
        if fitted:
            self.log.log("step_arguments_fitted", {
                "step_id": step.id, "tool": tool_name, "warnings": fitted,
            })
        step.action_spec["arguments"] = resolved
        return None

    def _execute_step(self, step: PlanStep) -> dict[str, Any] | None:  # noqa: PLR0911, PLR0912, PLR0915 — flat: depth 3, all 16 returns are guard clauses
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
        # Clear the scratch slot so the caller never sees a stale trigger.
        _step_trigger_tls.step_trigger = None

        tool_name = step.action_spec.get("tool_name")
        source_label = step.action_spec.get("source_label") or f"step:{step.id}"
        arguments = step.action_spec.get("arguments", {})

        # Track (tool, args) repetition across the run; logged only when the count first crosses the threshold.
        _tracker = getattr(self, "_step_repetition", None)
        if _tracker is not None and tool_name:
            _rep = _tracker.observe(tool_name, arguments)
            if _rep is not None:
                self.log.log("step_repetition_detected", _rep)

        # Memory cache short-circuit — only for a result the world cannot have
        # changed since (`core/cache_freshness.py`).
        from core.cache_freshness import cache_stamp
        stamp = cache_stamp(tool_name, arguments, self._file_read_workspace_root(), len(getattr(self, "compensation_log", None) or ()), getattr(self.memory, "_next_turn_index", None)) if tool_name else None
        if self.memory is not None and tool_name and stamp is not None:
            cached = self.memory.cache_lookup(tool_name, arguments)
            if cached is not None and cached.get("stamp") != stamp:
                self.log.log("memory_cache_stale", {"tool": tool_name, "arguments": arguments})
                cached = None
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
                    "issues": ["served from working-memory cache", "повтор: этот вызов уже сделан, мир с тех пор не менялся — действуй по результату или смени подход"],
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
            # The host's dry-run and the run's are ORed, never replaced: a task cannot take a host out of simulation.
            from core.run_context import run_demands_dry_run

            gateway = ActuationGateway(
                self.policy,
                path=self.gateway_path,
                dry_run=bool(self.gateway_dry_run) or run_demands_dry_run(),
                kill_switch=kill_switch,
                budget_snapshot=getattr(self, "gateway_budget_snapshot", None),
                readiness_blockers=getattr(self, "gateway_readiness_blockers", ()),
                check_readiness=bool(getattr(self, "gateway_check_readiness", False)),
                # Files of pending requests: a request's prohibition extends to them and no further.
                pending_approval_paths=getattr(self, "gateway_approval_paths", None),
            )
            gw = gateway.evaluate(action, registry=self.registry)
            self.log.log("gateway_decision", gw.to_log_payload())
            # Durable gateway decision receipt (kind="gateway"); never raises, observation only.
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
            assert decision is not None  # noqa: S101 — type narrowing, guarded above
            self.log.log("policy", decision)
        else:
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
            # PolicyGate flagged a risky action: ask the human via the ApprovalProvider; with no provider the safe default is to refuse.
            verdict = self._request_approval(
                action=action,
                step=step,
                source_label=source_label,
                policy_decision=decision,
                arguments=arguments,
            )
            if verdict != "approve":
                # Map the approval verdict back to a replan code so the planner can pick a safer alternative.
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
            return None

        result = self._call_tool(action)
        if result.status != "success":
            raw_error = result.error or failed_output_reason(result.output)
            # File absence (FileNotFoundError/IsADirectoryError) is a hard stop: the planner must NOT retry the path, search the web
            # or recreate the file; a dedicated FailureType says so.
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

        # Structured-failure reclassification: empty web_search results and shell_exec timeouts pass validate_output but need specific replan advice;
        # detected HERE, not in the tools, to keep tool contracts stable.
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

        # Safety pipeline: classify the output, emit secret_detected on SECRET, deep-redact BEFORE it reaches artifacts, cache or synthesizer.
        # Outer guard: a raising helper becomes an 'unknown' replan trigger instead of crashing the cycle.
        try:
            source_hint: SourceHint = _TOOL_SOURCE_HINTS.get(
                action.tool_name or "", "tool_output"
            )
            flat_output = _to_text(result.output)
            # Trusted internal tools: a bare credential word ("api_key") is not a leaked secret, so the soft keyword layer is off for them;
            # real credential SHAPES are still detected.
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

            # Injection guard: blocked content is dropped and triggers a replan; suspicious content is annotated and passed with a trust warning.
            # Exempt tools are listed above.
            if action.tool_name in _INJECTION_SCAN_EXEMPT:
                inj = None
            else:
                # Scan ONLY the untrusted payload, not framework envelope metadata (argv, compensation-plan text, timing), which tripped override
                # patterns on our own text (see untrusted_scan_view()).
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
                annotated = self._blocked_output_or_replan(
                    inj, step, action, tool_name, arguments, source_label, flat_output
                )
                if annotated is None:
                    return None
                result = result.model_copy(update={"output": annotated})
                flat_output = annotated
            if inj is not None and inj.verdict == "suspicious":
                # Wrap the output with a trust warning for the synthesizer; the output shape is kept because evidence is built from it.
                annotated = annotate_suspicious_output(action.tool_name, result.output, source_label)
                result = result.model_copy(update={"output": annotated})
                flat_output = _to_text(annotated)

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

                # Redact SECRET output before it reaches planner, synthesizer or memory cache.
                safe_output = redact_payload(result.output)

                if self.memory is not None and action.tool_name:
                    self.memory.cache_store(
                        tool_name=action.tool_name,
                        arguments=arguments,
                        output=safe_output,
                        label=source_label,
                        stamp=stamp,
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

                # Cache the REDACTED artifact for future turns.
                if self.memory is not None and action.tool_name:
                    self.memory.cache_store(
                        tool_name=action.tool_name,
                        arguments=arguments,
                        output=safe_output,
                        label=source_label,
                        stamp=stamp,
                    )

                return {
                    "label": source_label,
                    "tool": action.tool_name,
                    # Surfaced so the evidence factory can build a typed `Evidence` (source_id often depends on an argument).
                    "arguments": action.parameters,
                    "output": safe_output,
                    "issues": issues,
                    "data_class": cls_result.cls.value,
                }

            # PUBLIC/INTERNAL/SECRET-flagged data: cache the original output and return it as-is (secret_detected was already logged above).
            if self.memory is not None and action.tool_name:
                self.memory.cache_store(
                    tool_name=action.tool_name,
                    arguments=arguments,
                    output=result.output,
                    label=source_label,
                    stamp=stamp,
                )

        except Exception as exc:  # noqa: BLE001
            # Last-resort guard: a raising postprocessing step becomes an 'unknown' replan trigger.
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
        else:
            return {
                "label": source_label,
                "tool": action.tool_name,
                "arguments": action.parameters,
                "output": result.output,
                "issues": issues,
                "data_class": cls_result.cls.value,
            }

    def _call_tool(self, action: Action) -> ToolResult:
        assert action.tool_name is not None  # noqa: S101 — type narrowing, guarded above
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
        # What was read goes to the outgoing flow journal (core/egress_flow.py) for the gates.
        egress = getattr(self.policy, "egress", None)
        if egress is not None:
            egress.note(action.tool_name, result.output)
        # A successful tool call may carry a `compensation_plan`; the agent owns the registry (`rollback()` reads it LIFO inside the workspace sandbox).
        self._capture_compensation_plan(result)
        return result

    def _capture_compensation_plan(self, result: ToolResult) -> None:
        """Extract and register a `compensation_plan` from a successful tool result, logged as `compensation_registered` for audit."""
        if result.status != "success" or not isinstance(result.output, dict):
            return
        raw = result.output.get("compensation_plan")
        if not isinstance(raw, dict):
            return
        # Skip noop plans: they pollute rollback semantics and the audit log (the plan is still in the tool_result output).
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

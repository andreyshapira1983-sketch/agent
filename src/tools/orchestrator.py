"""
Tool orchestrator: run_tool for registry calls; Orchestrator for full autonomous cycle.

Autonomous cycle: observe → reason → plan → act → reflect → improve.
Uses: PolicyEngine (quotas, forbidden paths), audit log, planner, task queue,
ReflectionEngine, LearningManager, SelfTuning.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from src.tools.registry import call

_log = logging.getLogger(__name__)

_CODE_CHANGE_TOOLS = {
    "request_patch",
    "propose_file_edit",
    "write_file",
    "propose_patch",
    "validate_patch",
    "accept_patch",
}


def _strict_mode_enabled() -> bool:
    return (os.getenv("AGENT_STRICT_MODE") or "0").strip().lower() in ("1", "true", "yes", "on")


def _is_quiet_hours() -> bool:
    """
    True если сейчас «тихие часы» (не запускать тяжёлые автономные циклы).
    Конфиг: quiet_hours = "23-7" (с 23:00 до 07:00) или env QUIET_HOURS=23-7.
    Пусто/выключено — тихие часы не действуют.
    """
    try:
        from datetime import datetime
        from src.evolution.config_manager import load_config
        raw = (load_config().get("quiet_hours") or os.getenv("QUIET_HOURS") or "").strip()
        if not raw:
            return False
        parts = raw.replace(" ", "").split("-")
        if len(parts) != 2:
            return False
        start_h = int(parts[0])
        end_h = int(parts[1])
        now = datetime.now().hour
        if start_h <= end_h:
            return start_h <= now < end_h
        return now >= start_h or now < end_h
    except Exception:
        return False


def _is_successful_tool_result(
    result: str,
    tool_name: str | None = None,
    arguments: dict[str, Any] | None = None,
) -> bool:
    text = (result or "").strip()
    if not text:
        return False
    lowered = text.lower()
    error_prefixes = (
        "tool error:",
        "error:",
        "error ",
        "cannot ",
        "rejected",
        "failed",
        "invalid ",
        "validation failed",
        "patch must be validated",
    )
    if lowered.startswith(error_prefixes):
        return False
    if "no such file or directory" in lowered:
        return False
    if _strict_mode_enabled() and (tool_name or "") in _CODE_CHANGE_TOOLS:
        args = arguments or {}
        if "warning" in lowered:
            return False
        if (tool_name or "") in {"request_patch", "propose_file_edit"}:
            path = str(args.get("path") or "").replace("\\", "/").strip()
            if "diff saved to config/pending_patches/" not in lowered:
                return False
            if path and path not in text:
                return False
        if (tool_name or "") in {"write_file"}:
            if "written " not in lowered:
                return False
            if "sandbox tests passed" not in lowered:
                return False
        if (tool_name or "") in {"validate_patch"} and "validation passed" not in lowered:
            return False
        if (tool_name or "") in {"accept_patch"} and " applied to " not in lowered:
            return False
    return True


# Инструменты, которые по смыслу долгие: не считать их «tool_slow» при разумной длительности.
_SLOW_TOOLS_MIN_THRESHOLD_SEC = 25.0   # validate_patch, run_pytest — песочница
_REQUEST_PATCH_MIN_THRESHOLD_SEC = 6.0  # request_patch — чтение + LLM + валидация
_NETWORK_TOOLS_MIN_THRESHOLD_SEC = 5.0  # fetch_url, search_openlibrary — сеть


def _get_tool_warn_threshold_sec(tool_name: str = "") -> float:
    """Порог в секундах: при превышении — предупреждение «Tool slow». TOOL_SLOW_THRESHOLD_SEC в .env. Для validate_patch/run_pytest порог не ниже 25 с."""
    v = (os.getenv("TOOL_SLOW_THRESHOLD_SEC") or "").strip()
    base = 5.0
    if v:
        try:
            base = max(0.0, float(v))
        except ValueError:
            pass
    else:
        try:
            from src.evolution.config_manager import load_config
            base = float((load_config().get("tool_performance") or {}).get("warn_threshold_sec", 5.0))
        except Exception:
            pass
    if (tool_name or "") in ("validate_patch", "run_pytest"):
        return max(base, _SLOW_TOOLS_MIN_THRESHOLD_SEC)
    if (tool_name or "") == "request_patch":
        return max(base, _REQUEST_PATCH_MIN_THRESHOLD_SEC)
    if (tool_name or "") in ("fetch_url", "search_openlibrary"):
        return max(base, _NETWORK_TOOLS_MIN_THRESHOLD_SEC)
    return base


def run_tool(name: str, arguments: dict[str, Any] | None = None) -> str:
    kwargs = arguments or {}
    t0 = time.perf_counter()
    try:
        result = call(name, **kwargs)
        duration = time.perf_counter() - t0
        try:
            from src.monitoring.metrics import metrics
            metrics.log_tool_time(name, duration)
            threshold = _get_tool_warn_threshold_sec(name)
            if threshold > 0 and duration > threshold:
                _log.warning("Tool %s exceeded threshold: %.2fs > %.2fs", name, duration, threshold)
                try:
                    from src.hitl.audit_log import audit
                    audit("tool_slow", {"tool": name, "duration_sec": round(duration, 2), "threshold_sec": threshold})
                except Exception:
                    pass
                try:
                    from src.communication.telegram_alerts import send_alert
                    send_alert(f"⚠️ Tool slow: {name} — {duration:.2f}s (threshold {threshold}s)")
                except Exception:
                    pass
                try:
                    from src.personality.triggers import fire_trigger
                    fire_trigger("tool_slow", with_random=False)
                except Exception:
                    pass
        except Exception:
            pass
        return str(result)
    except Exception as e:
        duration = time.perf_counter() - t0
        try:
            from src.monitoring.metrics import metrics
            metrics.log_tool_time(name, duration)
        except Exception:
            pass
        try:
            from src.personality.triggers import fire_trigger
            fire_trigger("patch_failed", with_random=False)
        except Exception:
            pass
        return f"Tool error: {e}"


class Orchestrator:
    """
    Autonomous agent loop: observe → reason → plan → act → reflect → improve.
    Respects PolicyEngine quotas and forbidden paths; logs actions to audit.
    """

    def __init__(
        self,
        *,
        policy: Any = None,
        use_approval_layer: bool = False,
    ) -> None:
        self._policy = policy
        self._use_approval = use_approval_layer
        self._reflection = None
        self._learning_manager = None
        self._self_tuning = None

    def _get_reflection(self):
        if self._reflection is None:
            from src.reflection.reflection import ReflectionEngine
            self._reflection = ReflectionEngine()
        return self._reflection

    def _get_learning_manager(self):
        if self._learning_manager is None:
            from src.learning.learning_manager import LearningManager
            self._learning_manager = LearningManager()
        return self._learning_manager

    def _get_self_tuning(self):
        if self._self_tuning is None:
            from src.learning.self_tuning import SelfTuning
            self._self_tuning = SelfTuning()
        return self._self_tuning

    def _get_policy(self):
        if self._policy is None:
            from src.governance.policy_engine import PolicyEngine
            self._policy = PolicyEngine()
        return self._policy

    _cycle_index: int = 0

    def observe(self) -> dict[str, Any]:
        """Gather state: metrics, repo/test summary, inbox для дочерних агентов. Returns state dict for reason/plan."""
        from src.monitoring.metrics import get_metrics
        state: dict[str, Any] = {"metrics": get_metrics(), "_cycle_index": Orchestrator._cycle_index}
        reflection = self._get_reflection()
        state["self_assessment"] = reflection.self_assessment()
        state["sequence_trace"] = reflection.get_sequence_trace()
        try:
            from src.state.agent_state import get_state as get_agent_state
            agent_id = (get_agent_state() or {}).get("agent_id", "root")
            if agent_id and agent_id != "root":
                from src.agency.family_store import read_inbox
                state["inbox"] = read_inbox(agent_id, clear_after=False)
        except Exception:
            pass
        return state

    def reason(self, state: dict[str, Any]) -> str:
        """Analyze state and produce a goal string for the planner. Чередуем цели по циклу."""
        inbox = state.get("inbox") or []
        if inbox:
            return "process messages from family inbox and respond if needed"
        metrics = state.get("metrics") or {}
        errors = metrics.get("errors", 0)
        successes = metrics.get("successes", 0)
        assessment = state.get("self_assessment") or {}
        success_rate = assessment.get("success_rate", 100.0)
        if errors > 0 or success_rate < 80:
            return "reduce errors and improve success rate"
        idx = state.get("_cycle_index", 0) % 4
        goals = [
            "gather information and maintain quality",
            "maintain quality and monitor metrics",
            "check system metrics and time",
            "get metrics and current time",
        ]
        return goals[idx]

    def plan(self, goal: str) -> None:
        """Build plan from goal and enqueue tasks (tool steps) with priority and dependencies."""
        from src.planning.planner import make_plan
        from src.tasks.queue import enqueue
        from src.tasks.task_state import Task
        plan = make_plan(goal)
        prev_id: str | None = None
        for i, step in enumerate(plan.steps):
            task_id = f"step_{i}_{step.tool}"
            depends_on = [prev_id] if prev_id else []
            task = Task(
                id=task_id,
                payload={
                    "tool": step.tool,
                    "arguments": step.arguments or {},
                    "priority": 5,
                    "depends_on": depends_on,
                },
            )
            if not enqueue(task):
                break
            prev_id = task_id

    def act(self) -> list[dict[str, Any]]:
        """Dequeue and execute tasks; emotion-aware priority, policy check, audit. Returns outcomes."""
        from src.tasks.queue import dequeue_emotion_aware, size
        from src.hitl.audit_log import audit
        outcomes: list[dict[str, Any]] = []
        policy = self._get_policy()
        reflection = self._get_reflection()
        max_actions = policy.max_actions_per_cycle
        try:
            from src.personality.emotion_matrix import get_behavior_modifiers
            mods = get_behavior_modifiers()
            if mods.get("sleep_mode"):
                max_actions = 1
            elif mods.get("reduce_actions"):
                max_actions = max(1, max_actions // 2)
        except Exception:
            pass
        completed_task_ids: set[str] = set()
        while size() > 0 and policy.can_perform_action() and len(outcomes) < max_actions:
            try:
                from src.communication.autonomous_mode import is_stop_requested
                if is_stop_requested():
                    _log.info("act() stopped: user requested stop.")
                    break
            except Exception:
                pass
            task = dequeue_emotion_aware(completed_task_ids)
            if task is None:
                break
            tool_name = (task.payload or {}).get("tool", "")
            arguments = (task.payload or {}).get("arguments") or {}
            allowed, reason = policy.check_run_tool(tool_name, arguments)
            if not allowed:
                _log.warning("Policy denied tool %s: %s", tool_name, reason)
                outcomes.append({"task_id": task.id, "success": False, "reason": reason})
                completed_task_ids.add(task.id)
                continue
            # Проверка пути по политике (forbidden_prefixes: .cursor/, config/agent.json, src/main.py, hitl, governance)
            path_for_check = (arguments.get("path") or "").strip()
            if path_for_check and tool_name in ("write_file", "propose_file_edit"):
                allowed_path, path_reason = policy.check_apply_patch(path_for_check)
                if not allowed_path:
                    _log.warning("Policy denied path for %s: %s", tool_name, path_reason)
                    outcomes.append({"task_id": task.id, "success": False, "reason": path_reason})
                    completed_task_ids.add(task.id)
                    continue
            # Режим «применять только из песочницы»: в автономном цикле write_file/propose_file_edit блокируются
            try:
                from src.governance.safe_expand_mode import apply_sandbox_only_enabled
                if apply_sandbox_only_enabled() and tool_name in ("write_file", "propose_file_edit"):
                    outcomes.append({
                        "task_id": task.id,
                        "success": False,
                        "reason": "apply_sandbox_only: use propose_patch → validate_patch → accept_patch.",
                    })
                    completed_task_ids.add(task.id)
                    continue
            except Exception:
                pass
            if self._use_approval:
                from src.agency.autonomy_manager import needs_confirmation
                if needs_confirmation(f"run_tool:{tool_name}"):
                    _log.info("Skipping tool %s (requires confirmation)", tool_name)
                    outcomes.append({"task_id": task.id, "success": False, "reason": "pending_approval"})
                    completed_task_ids.add(task.id)
                    continue
            check_url = (arguments.get("url") or arguments.get("site") or "").strip()
            if check_url:
                try:
                    from src.policy.allowed_sites import is_site_allowed, notify_site_blocked
                    url_to_check = check_url if check_url.startswith("http") else f"https://{check_url}"
                    if not is_site_allowed(url_to_check):
                        _log.warning("Site not in allowed list, skipping task: %s", check_url[:100])
                        notify_site_blocked(url_to_check, task_id=task.id, tool=tool_name)
                        outcomes.append({
                            "task_id": task.id,
                            "success": False,
                            "reason": "site_not_allowed",
                            "url": check_url[:200],
                        })
                        completed_task_ids.add(task.id)
                        continue
                except Exception as e:
                    _log.debug("Allowed-sites check skipped: %s", e)
            try:
                from src.communication.telegram_alerts import send_agent_step
                short = (arguments.get("path") or arguments.get("patch_id") or str(arguments)[:50] or "")
                if short and len(str(short)) > 45:
                    short = str(short)[:42] + "..."
                send_agent_step("task_start", f"Начал: {tool_name}" + (f" ({short})" if short else ""))
            except Exception:
                pass
            result = run_tool(tool_name, arguments)
            success = _is_successful_tool_result(result, tool_name=tool_name, arguments=arguments)
            policy.record_action()
            audit("autonomous_act", {"task_id": task.id, "tool": tool_name, "success": success})
            if success:
                try:
                    from src.communication.telegram_alerts import send_agent_step
                    if tool_name in ("accept_patch", "propose_file_edit", "request_patch"):
                        path_preview = (arguments.get("path") or arguments.get("patch_id") or "")[:40]
                        send_agent_step("patch", "Применил патч" + (f" ({path_preview})" if path_preview else ""))
                    elif tool_name in ("get_improvement_plan", "analyze_self_model"):
                        send_agent_step("report", "Сгенерировал отчёт" + (" (план улучшений)" if tool_name == "get_improvement_plan" else " (самоанализ)"))
                except Exception:
                    pass
            reflection.record_action(task.id, task.payload)
            reflection.analyze_action({
                "action": task.id,
                "success": success,
                "expected_result": result[:200],
            })
            if success:
                try:
                    from src.monitoring.metrics import metrics
                    metrics.record_task_solved(
                        task_id=task.id,
                        tool_name=tool_name,
                        note=str((task.payload or {}).get("arguments") or {})[:120],
                    )
                except Exception:
                    pass
            outcomes.append({"task_id": task.id, "success": success, "result_preview": result[:150]})
            completed_task_ids.add(task.id)
        return outcomes

    def reflect(self) -> dict[str, Any]:
        """Analyze outcomes; return self_assessment and sequence trace."""
        reflection = self._get_reflection()
        return {
            "self_assessment": reflection.self_assessment(),
            "sequence_trace": reflection.get_sequence_trace(),
        }

    def improve(self, action_data: dict[str, Any]) -> dict[str, Any]:
        """Apply learning rules and feedback tuning. Returns improvements list.
        Раз в цикл выводит до 2 правил из feedback с низким рейтингом (rules_safety); они попадают в контекст LLM."""
        lm = self._get_learning_manager()
        st = self._get_self_tuning()
        adjusted = lm.apply_rules(action_data)
        feedback = {"success": adjusted.get("success", True)}
        improvements = list(st.analyze_feedback(feedback))
        try:
            from src.learning.rule_derivation import derive_rules_from_feedback
            added = derive_rules_from_feedback(max_rules=2)
            if added:
                improvements.extend([f"Derived rule: {r[:80]}..." for r in added])
        except Exception:
            pass
        return {"adjusted": adjusted, "improvements": improvements}

    def run_cycle(self) -> dict[str, Any]:
        """Single cycle: observe → reason → plan → act → reflect → improve. Returns summary."""
        policy = self._get_policy()
        if not policy.can_start_cycle():
            return {"status": "quota_exceeded", "message": "Max cycles or actions reached."}
        if _is_quiet_hours():
            return {"status": "skipped", "message": "Тихие часы (цикл пропущен).", "goal": "", "outcomes_count": 0}
        from src.hitl.audit_log import audit
        try:
            from src.governance.patch_guard import advance_cycle as advance_patch_cycle
            advance_patch_cycle()
        except Exception:
            pass
        try:
            from src.governance.task_guard import advance_cycle as advance_task_cycle
            advance_task_cycle()
        except Exception:
            pass
        try:
            from src.tasks.queue import purge_aged
            dropped = purge_aged()
            if dropped > 0:
                _log.info("Task guard: purged %s aged tasks.", dropped)
        except Exception:
            pass
        policy.reset_cycle()
        audit("autonomous_cycle_start", {"phase": "observe"})
        state = self.observe()
        goal = self.reason(state)
        # Визор: в каждом цикле показываем цель (чтобы не было «—», даже если очередь не пуста)
        try:
            from src.hitl.initiative_visor_state import set_planner_choice
            set_planner_choice("цель_цикла", (goal or "")[:280])
        except Exception:
            pass
        self.plan(goal)
        try:
            from src.tasks.queue import size
            from src.tasks.task_creator import try_generate_and_enqueue
            from src.planning.planner_loop import think_next_action, enqueue_planner_action
            if size() == 0:
                action_id, reason = think_next_action(state)
                try:
                    from src.hitl.initiative_visor_state import set_planner_choice
                    set_planner_choice(action_id or "continue_queue", reason or "")
                except Exception:
                    pass
                if action_id and action_id != "continue_queue" and reason:
                    if action_id in ("share_with_user", "communicate_with_user"):
                        try:
                            from src.communication.proactive_planner import try_send_proactive
                            try_send_proactive()
                        except Exception:
                            pass
                    added = enqueue_planner_action(action_id, state)
                    if added > 0 or action_id in ("share_with_user", "communicate_with_user"):
                        _log.debug("Planner chose %s: %s", action_id, reason)
                        try:
                            from src.communication.telegram_alerts import send_autonomous_event
                            send_autonomous_event(f"Мысль: {reason}\nДелаю: {action_id}.", urgent=False)
                        except Exception:
                            pass
                if size() == 0 and try_generate_and_enqueue(state) > 0:
                    _log.debug("Queue was empty: generated tasks from emotions/metrics.")
        except Exception:
            pass
        outcomes = self.act()
        reflection_summary = self.reflect()
        action_data = {"success": reflection_summary["self_assessment"].get("success_rate", 0) >= 50}
        improve_summary = self.improve(action_data)
        policy.record_cycle_done()
        audit("autonomous_cycle_end", {"goal": goal, "outcomes": len(outcomes)})
        try:
            from src.communication.telegram_alerts import send_autonomous_event
            from src.hitl.audit_log import get_audit_tail
            from src.personality.triggers import fire_trigger
            from src.personality.emotion_matrix import decay_tick
            success_count = sum(1 for o in outcomes if o.get("success"))
            fail_count = len(outcomes) - success_count
            tail = get_audit_tail(150)
            start_idx = -1
            for i, e in enumerate(tail):
                if e.get("action") == "autonomous_cycle_start":
                    start_idx = i
            patches_in_cycle = 0
            if start_idx >= 0:
                for e in tail[start_idx + 1 :]:
                    if e.get("action") in ("apply_patch_with_approval", "propose_file_edit"):
                        patches_in_cycle += 1
            success_rate = success_count / len(outcomes) if outcomes else 1.0
            if success_rate >= 0.5:
                fire_trigger("cycle_end_success", with_random=False)
            else:
                fire_trigger("cycle_end_fail", with_random=False)
            decay_tick()
            improvements_count = len(improve_summary.get("improvements", []))
            summary_lines = [
                "✅ [Автономное действие] Цикл завершён.",
                f"Цель: {goal}",
                f"Действий: {len(outcomes)}, успешных: {success_count}, ошибок: {fail_count}.",
            ]
            if patches_in_cycle:
                summary_lines.append(f"Патчи/правки в цикле: {patches_in_cycle}.")
            emotion_context = {"improvements": improvements_count, "patches_in_cycle": patches_in_cycle}
            if fail_count:
                emotion_context["patch_failed"] = 1
            send_autonomous_event("\n".join(summary_lines), emotion_context=emotion_context)
        except Exception:
            pass
        try:
            from src.monitoring.metrics import get_metrics
            tt = get_metrics().get("tool_times")
            if tt:
                _log.info("Cycle tool times: %s", " ".join(f"{k}={v['last_sec']}s" for k, v in tt.items()))
        except Exception:
            pass
        Orchestrator._cycle_index += 1
        return {
            "status": "ok",
            "goal": goal,
            "outcomes_count": len(outcomes),
            "self_assessment": reflection_summary["self_assessment"],
            "improvements": improve_summary.get("improvements", []),
        }

    def run(
        self,
        max_cycles: int | None = None,
        export_performance_every_n_cycles: int = 0,
        check_alerts_every_n_cycles: int = 0,
    ) -> None:
        """
        Run autonomous loop until quota or max_cycles. Use Ctrl+C to stop.
        If export_performance_every_n_cycles > 0, export_performance_summary() is called every N cycles.
        If check_alerts_every_n_cycles > 0, check_performance_alerts() is called every N cycles; results are logged.
        """
        policy = self._get_policy()
        limit = max_cycles if max_cycles is not None else policy.max_cycles
        cycle = 0
        while cycle < limit and policy.can_start_cycle():
            summary = self.run_cycle()
            _log.info("Cycle %s: %s", cycle + 1, summary.get("status"))
            if summary.get("status") == "quota_exceeded":
                break
            cycle += 1
            if export_performance_every_n_cycles > 0 and cycle % export_performance_every_n_cycles == 0:
                try:
                    from src.monitoring.metrics import export_performance_summary
                    out = export_performance_summary()
                    _log.info("Performance export: %s", out)
                except Exception as e:
                    _log.debug("Performance export skipped: %s", e)
            if check_alerts_every_n_cycles > 0 and cycle % check_alerts_every_n_cycles == 0:
                try:
                    from src.monitoring.metrics import check_performance_alerts
                    out = check_performance_alerts()
                    _log.info("Performance alerts: %s", out)
                except Exception as e:
                    _log.debug("Performance alerts check skipped: %s", e)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    orchestrator = Orchestrator()
    # Run a single cycle by default to avoid infinite loop
    orchestrator.run_cycle()

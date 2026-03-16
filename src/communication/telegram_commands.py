"""
Формирование ответов для команд Telegram: /status, /log.
"""
from __future__ import annotations

from typing import Any


# Типы действий для фильтра /log
AUTONOMOUS_ACTIONS = (
    "autonomous_cycle_start",
    "autonomous_cycle_end",
    "autonomous_act",
    "tool_slow",
    "performance_alert",
    "apply_patch_with_approval",
    "propose_file_edit",
    "write_file",
    "update_prompt_rules",
    "update_self_model",
    "generate_module_skeleton",
)

# Модуль по типу действия (для фильтра /log по модулю)
ACTION_MODULE: dict[str, str] = {
    "apply_patch_with_approval": "self_model",
    "propose_file_edit": "file_tools",
    "write_file": "file_tools",
    "update_prompt_rules": "evolution_tools",
    "update_self_model": "self_model",
    "generate_module_skeleton": "self_model",
    "autonomous_cycle_start": "orchestrator",
    "autonomous_cycle_end": "orchestrator",
    "autonomous_act": "orchestrator",
    "tool_slow": "monitoring",
    "performance_alert": "monitoring",
}

# Приоритет по типу действия (high / medium / low)
ACTION_PRIORITY: dict[str, str] = {
    "tool_slow": "high",
    "performance_alert": "high",
    "apply_patch_with_approval": "high",
    "propose_file_edit": "medium",
    "write_file": "medium",
    "autonomous_cycle_end": "medium",
    "autonomous_act": "medium",
    "update_prompt_rules": "medium",
    "autonomous_cycle_start": "low",
    "update_self_model": "low",
    "generate_module_skeleton": "low",
}

MODULE_ALIASES = ("self_model", "file_tools", "evolution_tools", "orchestrator", "monitoring")
PRIORITY_ALIASES = ("high", "medium", "low")


def get_help_text() -> str:
    """Краткий список команд для /help."""
    lines = [
        "Команды бота:",
        "/help — этот список",
        "/status — статус агента (квоты, последний цикл, очередь)",
        "/tasks или /queue — очередь задач",
        "/log [N] [action] [module] [priority] — последние действия",
        "/mood или /emotions — эмоциональное состояние",
        "/guard — защитные контуры (patch_guard, task_guard, evolution lock)",
        "/autonomous — включить автономный режим (циклы в фоне)",
        "/stop или /cancel — остановить автономный режим",
        "/safe_expand — расширяться только через песочницу (propose_patch → validate → accept_patch)",
        "/apply_sandbox_only — применять только помеченное из песочницы (блокирует write_file/propose_file_edit в автономном режиме)",
        "/apply_validated — применить все проверенные патчи из песочницы (без программирования)",
        "/remind завтра 18:00 купить молоко — напоминание (или: через 2 часа позвонить)",
        "",
        "Обычное сообщение — агент отвечает и при необходимости выполняет действия на ПК.",
    ]
    return "\n".join(lines)


def get_agent_status() -> str:
    """
    Текущий статус агента: квоты, последний цикл, ошибки, метрики.
    """
    from datetime import datetime, timezone

    lines: list[str] = []
    ts = datetime.now(timezone.utc).isoformat()
    try:
        from src.governance.policy_engine import PolicyEngine
        policy = PolicyEngine()
        q = policy.get_quota_status()
        lines.append("Квоты:")
        lines.append(
            "  "
            f"Циклов: {q['cycles_done']}/{q['max_cycles']} [source=policy_engine timestamp={ts}], "
            f"действий в цикле: {q['actions_this_cycle']}/{q['max_actions_per_cycle']} "
            f"[source=policy_engine timestamp={ts}]"
        )
        lines.append(
            "  "
            f"Может начать цикл: {'да' if q['can_start_cycle'] else 'нет'} "
            f"[source=policy_engine timestamp={ts}], "
            f"может выполнить действие: {'да' if q['can_perform_action'] else 'нет'} "
            f"[source=policy_engine timestamp={ts}]"
        )
        try:
            from src.communication.autonomous_mode import is_autonomous_running, get_cycles_done
            if is_autonomous_running():
                lines.append(f"  Автономный режим: включён (циклов в этой сессии: {get_cycles_done()}). Остановить: /stop")
            elif q.get("can_start_cycle"):
                lines.append("  Один цикл: напиши «да» или «запусти». Фоновый автомат: /autonomous")
        except Exception:
            if q.get("can_start_cycle"):
                lines.append("  Напиши «да» или «запусти» — один цикл. Фоновый автомат: /autonomous")
        try:
            from src.governance.safe_expand_mode import get_status as get_safe_expand_status
            se = get_safe_expand_status()
            if se.get("safe_expand") or se.get("apply_sandbox_only"):
                modes = [k for k, v in se.items() if v]
                lines.append(f"  Режимы: {', '.join(modes)} (/safe_expand, /apply_sandbox_only — выкл)")
        except Exception:
            pass
    except Exception:
        lines.append("Квоты: не удалось загрузить PolicyEngine.")
    try:
        from src.hitl.audit_log import get_audit_tail
        tail = get_audit_tail(20)
        cycle_ends = [e for e in tail if e.get("action") == "autonomous_cycle_end"]
        if cycle_ends:
            last = cycle_ends[-1]
            d = last.get("details") or {}
            lines.append(
                f"Последний цикл: цель — {d.get('goal', '—')}, "
                f"исходов: {d.get('outcomes', 0)} [source=audit_log timestamp={ts}]"
            )
        acts = [e for e in tail if e.get("action") == "autonomous_act"]
        if acts:
            last_act = acts[-1]
            d = last_act.get("details") or {}
            lines.append(f"Последнее действие: {d.get('tool', '—')} (успех: {d.get('success', '—')})")
        patches = [e for e in tail if e.get("action") in ("apply_patch_with_approval", "propose_file_edit")]
        if patches:
            lines.append(f"Патчи/правки за сессию: {len(patches)} [source=audit_log timestamp={ts}]")
    except Exception:
        lines.append("Audit: не удалось прочитать.")
    try:
        from src.monitoring.metrics import get_metrics
        m = get_metrics()
        lines.append(f"Ошибки (всего): {m.get('errors', 0)} [source=runtime_metrics timestamp={ts}]")
        if m.get("last_duration_sec") is not None:
            lines.append(
                f"Последний ответ: {m['last_duration_sec']} с [source=runtime_metrics timestamp={ts}]"
            )
        quality = m.get("quality") or {}
        if quality:
            lines.append(
                "Качество: "
                f"решено задач={quality.get('tasks_solved', 0)}, "
                f"принято патчей={quality.get('accepted_patches', 0)}, "
                f"успешных ремонтов={quality.get('successful_repairs', 0)}, "
                f"проваленных ремонтов={quality.get('failed_repairs', 0)} "
                f"[source=runtime_metrics timestamp={ts}]"
            )
            ratio = quality.get("test_pass_ratio")
            if ratio is not None:
                lines.append(
                    f"Тесты: pass_ratio={ratio} ({quality.get('test_runs_passed', 0)}/{quality.get('test_runs_total', 0)}) "
                    f"[source=runtime_metrics timestamp={ts}]"
                )
    except Exception:
        lines.append("Метрики: не удалось прочитать.")
    try:
        from src.monitoring.system_metrics import get_system_metrics_snapshot
        sm = get_system_metrics_snapshot(force_refresh=True, ttl_sec=5, top_n=3)
        source = sm.get("source", "unknown")
        sm_ts = sm.get("timestamp_utc", ts)
        if sm.get("ok"):
            lines.append(
                f"CPU: {sm.get('cpu_percent')}% [source={source} timestamp={sm_ts}]"
            )
            lines.append(
                f"RAM: {sm.get('ram_percent')}% [source={source} timestamp={sm_ts}]"
            )
            top = sm.get("top_processes") or []
            if top:
                lines.append("Топ процессов по CPU:")
                for p in top[:3]:
                    lines.append(
                        f"  pid={p.get('pid')} name={p.get('name')} cpu={p.get('cpu_percent')}% "
                        f"rss={p.get('rss_mb')} MB [source={source} timestamp={sm_ts}]"
                    )
        else:
            lines.append(
                f"System metrics unavailable [source={source} timestamp={sm_ts}]: {sm.get('error', 'unknown error')}"
            )
    except Exception as e:
        lines.append(f"System metrics: не удалось прочитать ({e}).")
    try:
        from src.tasks.queue import size
        from src.governance.evolution_lock import get_holder
        qsize = size()
        holder = get_holder()
        guard_line = (
            f"Очередь: {qsize} [source=task_queue timestamp={ts}]. "
            f"Evolution lock: {'занят' if holder else 'свободен'} [source=evolution_lock timestamp={ts}]. "
            "Подробнее: /guard"
        )
        lines.append(guard_line)
    except Exception:
        lines.append("Guard: /guard — статус защитных контуров.")
    return "\n".join(lines) if lines else "Нет данных о статусе."


def get_emotion_status() -> str:
    """Текущая эмоциональная матрица для /mood."""
    try:
        from src.personality.emotion_matrix import get_state, get_dominant, get_intensity
        state = get_state()
        name, value = get_dominant()
        intensity = get_intensity(value)
        lines = ["Эмоции (0–1):"]
        for k, v in state.items():
            bar = "█" * int(v * 10) + "░" * (10 - int(v * 10))
            lines.append(f"  {k}: {v:.2f} {bar}")
        lines.append(f"Доминанта: {name} ({intensity}, {value:.2f})")
        return "\n".join(lines)
    except Exception as e:
        return f"Не удалось загрузить эмоции: {e}"


# Короткие реплики, которые считаются согласием «запусти цикл» (после /status «Может начать цикл: да»).
START_CYCLE_INTENTS = (
    "да", "yes", "yeah", "ок", "окей", "ok", "okay",
    "запусти", "начни", "поехали", "давай", "старт", "run", "go",
    "начни цикл", "запусти цикл", "run cycle", "давай цикл",
)


def is_start_cycle_intent(text: str) -> bool:
    """Проверить, что пользователь просит запустить один цикл (например, в ответ на /status)."""
    t = (text or "").strip().lower()
    if not t:
        return False
    if t in START_CYCLE_INTENTS:
        return True
    for intent in START_CYCLE_INTENTS:
        if len(intent) > 3 and t.startswith(intent):
            return True
    return False


def get_guard_status() -> str:
    """
    Статус защитных контуров: patch_guard, task_guard, evolution lock.
    Для команды /guard в Telegram.
    """
    lines: list[str] = []
    try:
        from src.governance.patch_guard import _load_state
        state = _load_state()
        cycle = state.get("global_cycle", 0)
        files = state.get("files") or {}
        lines.append(f"Patch guard: цикл {cycle}, файлов в истории: {len(files)}")
    except Exception:
        lines.append("Patch guard: не удалось загрузить.")
    try:
        from src.tasks.queue import size
        from src.governance.task_guard import (
            get_current_cycle,
            MAX_TASK_QUEUE,
            MAX_TASKS_PER_CYCLE,
            MAX_EVOLUTION_PATCHES_PER_CYCLE,
        )
        get_current_cycle()
        qsize = size()
        lines.append(f"Task guard: очередь {qsize}/{MAX_TASK_QUEUE or '∞'}, макс. задач за цикл: {MAX_TASKS_PER_CYCLE or '∞'}, макс. accept за цикл: {MAX_EVOLUTION_PATCHES_PER_CYCLE or '∞'}")
    except Exception:
        lines.append("Task guard: не удалось загрузить.")
    try:
        from src.governance.evolution_lock import get_holder
        holder = get_holder()
        if holder:
            lines.append(f"Evolution lock: занят агентом {holder} (один патч в момент времени).")
        else:
            lines.append("Evolution lock: свободен.")
    except Exception:
        lines.append("Evolution lock: не удалось загрузить.")
    try:
        from src.governance.safe_expand_mode import get_status as get_safe_expand_status
        se = get_safe_expand_status()
        if se.get("safe_expand") or se.get("apply_sandbox_only"):
            parts = [k for k, v in se.items() if v]
            lines.append(f"Режимы расширения: {', '.join(parts)} (только песочница/помеченное)")
        else:
            lines.append("Режимы расширения: выкл. (/safe_expand, /apply_sandbox_only)")
    except Exception:
        pass
    try:
        from src.agency.supervisor import get_family_tree
        from src.state.agent_state import get_state
        agent_id = (get_state() or {}).get("agent_id", "root")
        tree = get_family_tree(agent_id)
        children = tree.get("children") or []
        if children:
            lines.append(f"Семья: я {tree['self'].get('name', agent_id)}, детей: {len(children)}")
        else:
            lines.append("Семья: только корневой агент (create_agent_family — создать детей).")
    except Exception:
        lines.append("Семья: не удалось загрузить.")
    return "\n".join(lines) if lines else "Нет данных о guard."


def get_apply_validated_result() -> str:
    """
    Применить все проверенные (validated) патчи из песочницы. Для команды /apply_validated.
    Пользователю не нужно программировать — одна команда встраивает то, что агент уже проверил.
    """
    try:
        from src.evolution.safety import get_validated_patch_ids, apply_all_validated
        ids = get_validated_patch_ids()
        if not ids:
            return "Нет проверенных патчей. Сначала агент должен: propose_patch → validate_patch (тесты в песочнице). После валидации снова отправь /apply_validated."
        results = apply_all_validated(max_n=20)
        ok = [r for r in results if r.get("status") == "ok"]
        err = [r for r in results if r.get("status") != "ok"]
        lines = [f"Применено патчей: {len(ok)} из {len(results)}."]
        for r in ok:
            lines.append(f"  ✅ {r.get('target_path', r.get('patch_id', ''))}")
        for r in err[:5]:
            lines.append(f"  ❌ {r.get('target_path', r.get('patch_id', ''))}: {str(r.get('message', ''))[:80]}")
        if len(err) > 5:
            lines.append(f"  … и ещё {len(err) - 5} с ошибками.")
        return "\n".join(lines)
    except Exception as e:
        return f"Ошибка при применении: {e}"


def get_queue_status(max_tasks: int = 20) -> str:
    """Очередь задач: размер и снимок следующих задач (для /tasks, /queue)."""
    try:
        from src.tasks.queue import size, peek
        n = size()
        lines = [f"Очередь: {n} задач(и)."]
        if n > 0:
            for i, t in enumerate(peek(max_tasks), 1):
                lines.append(f"  {i}. {t.get('id', '—')} → {t.get('tool', '—')} ({t.get('arguments_preview', '')})")
        return "\n".join(lines)
    except Exception as e:
        return f"Не удалось прочитать очередь: {e}"


def get_agent_log(
    n: int = 30,
    action_filter: str | list[str] | None = None,
    module_filter: str | None = None,
    priority_filter: str | None = None,
) -> str:
    """
    Последние автономные действия из audit.
    action_filter: один тип или список.
    module_filter: self_model | file_tools | evolution_tools | orchestrator | monitoring.
    priority_filter: high | medium | low.
    """
    try:
        from src.hitl.audit_log import get_audit_tail
        tail = get_audit_tail(n * 2 if (module_filter or priority_filter) else n, action_filter=action_filter)
    except Exception:
        return "Не удалось прочитать audit."
    if module_filter or priority_filter:
        filtered: list[dict] = []
        for e in tail:
            act = e.get("action", "")
            if module_filter and ACTION_MODULE.get(act) != module_filter:
                continue
            if priority_filter and ACTION_PRIORITY.get(act) != priority_filter:
                continue
            filtered.append(e)
        tail = filtered[-n:]
    if not tail:
        return "Нет записей." + (" (попробуйте без фильтра или другой тип.)" if (action_filter or module_filter or priority_filter) else "")
    lines: list[str] = []
    for e in tail:
        ts = (e.get("ts") or "")[:19].replace("T", " ")
        action = e.get("action", "")
        details = e.get("details") or {}
        if isinstance(details, dict) and details:
            parts = [f"{k}={v}" for k, v in list(details.items())[:5]]
            detail_str = ", ".join(parts)
        else:
            detail_str = str(details)[:80]
        lines.append(f"[{ts}] {action}: {detail_str}")
    return "\n".join(lines)

"""
Формирование ответов для команд Telegram: /status, /quality, /quality_export, /log.
"""
from __future__ import annotations

import json
import re
from pathlib import Path


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


_GREET_RE = re.compile(r"^(привет|здравствуй|здравствуйте|добрый\s+(день|вечер|утро)|hello|hi|hey)\b", re.IGNORECASE)
_SMALLTALK_RE = re.compile(
    r"(как\s+дела|что\s+у\s+тебя\s+происходит|что\s+делаешь|чем\s+занят|как\s+ты|что\s+нового)",
    re.IGNORECASE,
)
_READING_LOG_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "reading_log.json"


def _is_smalltalk_message(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if _GREET_RE.search(t):
        return True
    return bool(_SMALLTALK_RE.search(t))


def _extract_last_user_topic(history: list[dict[str, str]]) -> str:
    """Вытащить последнюю осмысленную пользовательскую тему из short-term памяти."""
    for item in reversed(history or []):
        if item.get("role") != "user":
            continue
        content = (item.get("content") or "").strip()
        low = content.lower()
        if len(content) < 6:
            continue
        if _is_smalltalk_message(content):
            continue
        if low in ("ок", "ага", "да", "нет", "понял", "понятно"):
            continue
        return content[:120]
    return ""


def _build_live_brief() -> str:
    """Короткая живая сводка: что агент делал недавно и что сейчас в очереди."""
    parts: list[str] = []
    try:
        from src.communication.autonomous_mode import is_autonomous_running

        parts.append("автономный режим включён" if is_autonomous_running() else "автономный режим сейчас выключен")
    except Exception:
        pass
    try:
        from src.hitl.audit_log import get_audit_tail

        tail = get_audit_tail(40)
        last_goal = ""
        last_tool = ""
        for e in reversed(tail):
            if e.get("action") == "autonomous_cycle_end":
                d = e.get("details") or {}
                last_goal = str(d.get("goal") or "").strip()
                if last_goal:
                    break
        for e in reversed(tail):
            if e.get("action") == "autonomous_act":
                d = e.get("details") or {}
                tool = str(d.get("tool") or "").strip()
                ok = d.get("success")
                if tool:
                    last_tool = f"последнее действие: {tool} (успех: {ok})"
                    break
        if last_goal:
            parts.append(f"последняя цель: {last_goal[:120]}")
        if last_tool:
            parts.append(last_tool)
    except Exception:
        pass
    try:
        from src.tasks.queue import size

        parts.append(f"в очереди задач: {size()}")
    except Exception:
        pass
    return "; ".join(parts)


def _extract_recent_learning_note() -> str:
    """Короткая заметка из памяти чтения/интернета (data/reading_log.json)."""
    try:
        if not _READING_LOG_PATH.exists():
            return ""
        raw = json.loads(_READING_LOG_PATH.read_text(encoding="utf-8"))
        entries = raw.get("entries") or []
        if not entries:
            return ""
        last = entries[-1]
        title = str(last.get("title") or "").strip()
        summary = str(last.get("summary") or "").strip()
        if summary:
            return summary[:140]
        if title:
            return f"недавно изучал: {title[:120]}"
    except Exception:
        return ""
    return ""


def get_human_memory_reply(user_id: str, text: str) -> str | None:
    """
    Человеческий ответ для small talk с опорой на short-term память и живое состояние.
    Возвращает None, если это не small talk и нужно стандартное выполнение через LLM.
    """
    if not _is_smalltalk_message(text):
        return None
    try:
        from src.memory import short_term

        history = short_term.get_messages(user_id)
    except Exception:
        history = []

    last_topic = _extract_last_user_topic(history)
    live = _build_live_brief()
    learning_note = _extract_recent_learning_note()
    low = (text or "").strip().lower()

    if _GREET_RE.search(text or ""):
        lines = ["Привет. Я на связи."]
        if last_topic:
            lines.append(f"Помню из нашего разговора: «{last_topic}»." )
        if live:
            lines.append(f"Сейчас у меня так: {live}.")
        if learning_note:
            lines.append(f"Из того, что изучил и запомнил: {learning_note}.")
        lines.append("Как ты сам? Если хочешь, продолжим с того места, где остановились.")
        return " ".join(lines)

    if "как дела" in low or "как ты" in low:
        lines = ["У меня рабочий темп, я в контексте нашего диалога."]
        if live:
            lines.append(f"По состоянию: {live}.")
        if learning_note:
            lines.append(f"Из памяти изученного: {learning_note}.")
        if last_topic:
            lines.append(f"И помню твою тему: «{last_topic}».")
        lines.append("Как у тебя дела?")
        return " ".join(lines)

    lines = []
    if live:
        lines.append(f"Сейчас происходит вот что: {live}.")
    else:
        lines.append("Я на связи и готов продолжать разговор.")
    if last_topic:
        lines.append(f"Из памяти держу нашу тему: «{last_topic}».")
    if learning_note:
        lines.append(f"Из изученного недавно: {learning_note}.")
    lines.append("Если хочешь, просто расскажи, что сейчас важно для тебя, и я подхвачу.")
    return " ".join(lines)


def get_help_text() -> str:
    """Краткий список команд для /help."""
    lines = [
        "Команды бота:",
        "/help — этот список",
        "/status — статус агента (квоты, последний цикл, очередь)",
        "/quality — quality-метрики и последние ремонты/патчи",
        "/quality_export [json|text|full] — выгрузить quality-отчёт в файл",
        "/reset_quality — сбросить quality-метрики и историю",
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
        "Команды не обязательны: можно писать обычными фразами.",
        "Примеры: «покажи качество», «выгрузи полный отчёт качества», «сбрось метрики качества».",
        "Обычное сообщение — агент отвечает и при необходимости выполняет действия на ПК.",
    ]
    return "\n".join(lines)


def get_quality_status() -> str:
    """Quality-метрики и краткая история последних ремонтов/патчей."""
    from src.monitoring.metrics import get_metrics

    m = get_metrics()
    q = (m.get("quality") or {}) if isinstance(m, dict) else {}
    lines = ["Quality:"]
    lines.append(f"  Решено задач: {q.get('tasks_solved', 0)}")
    lines.append(f"  Принято патчей: {q.get('accepted_patches', 0)}")
    lines.append(f"  Успешных ремонтов: {q.get('successful_repairs', 0)}")
    lines.append(f"  Проваленных ремонтов: {q.get('failed_repairs', 0)}")
    ratio = q.get("test_pass_ratio")
    if ratio is None:
        lines.append("  Тесты: данных пока нет")
    else:
        lines.append(f"  Тесты: pass_ratio={ratio} ({q.get('test_runs_passed', 0)}/{q.get('test_runs_total', 0)})")
    history = q.get("recent_history") or []
    if history:
        lines.append("Последние события:")
        for item in history[-5:]:
            event_type = item.get("event_type", "event")
            status = item.get("status", "unknown")
            target = item.get("target_path") or "-"
            patch_id = item.get("patch_id") or "-"
            note = item.get("note") or ""
            suffix = f"; note={note}" if note else ""
            lines.append(f"  {event_type}: {status}; path={target}; patch={patch_id}{suffix}")
    else:
        lines.append("Последние события: пока нет")
    return "\n".join(lines)


def get_weekly_quality_summary() -> str:
    """Краткая сводка качества за доступную историю последних событий и текущие счётчики."""
    from src.monitoring.metrics import get_metrics

    m = get_metrics()
    q = (m.get("quality") or {}) if isinstance(m, dict) else {}
    history = q.get("recent_history") or []
    accepted = sum(1 for item in history if item.get("event_type") == "accepted_patch" and item.get("status") == "ok")
    repaired_ok = sum(1 for item in history if item.get("event_type") == "repair_attempt" and item.get("status") == "ok")
    repaired_failed = sum(1 for item in history if item.get("event_type") == "repair_attempt" and item.get("status") == "failed")
    tasks = sum(1 for item in history if item.get("event_type") == "task_solved")
    test_total = int(q.get("test_runs_total", 0) or 0)
    test_passed = int(q.get("test_runs_passed", 0) or 0)
    pass_ratio = q.get("test_pass_ratio")

    better_parts: list[str] = []
    broken_parts: list[str] = []
    risk_parts: list[str] = []

    if accepted > 0:
        better_parts.append(f"принято патчей: {accepted}")
    if repaired_ok > repaired_failed:
        better_parts.append(f"ремонты чаще успешны ({repaired_ok} vs {repaired_failed})")
    if pass_ratio is not None and float(pass_ratio) >= 0.75:
        better_parts.append(f"тесты держатся на хорошем уровне ({pass_ratio})")

    if repaired_failed > 0:
        broken_parts.append(f"было неуспешных ремонтов: {repaired_failed}")
    if pass_ratio is not None and float(pass_ratio) < 0.75:
        broken_parts.append(f"просадка test pass ratio до {pass_ratio}")
    if test_total == 0:
        broken_parts.append("нет свежих прогонов тестов")

    if repaired_failed >= repaired_ok and repaired_failed > 0:
        risk_parts.append("риск повторных регрессий в блоках саморемонта")
    if accepted == 0 and tasks > 0:
        risk_parts.append("зафиксированы решённые задачи без подтверждённых патчей")
    if test_total > 0 and test_passed < test_total:
        risk_parts.append("часть тестов падает, перед релизом нужен прогон")
    if not risk_parts:
        risk_parts.append("критичный риск не выявлен, но нужен регулярный контроль")

    lines = ["Недельная сводка качества:"]
    lines.append(f"  Решено задач: {q.get('tasks_solved', 0)} (важных в истории: {tasks})")
    lines.append(f"  Принято патчей: {q.get('accepted_patches', 0)} (в истории: {accepted})")
    lines.append(f"  Ремонты: успешных={q.get('successful_repairs', 0)} неуспешных={q.get('failed_repairs', 0)}")
    if pass_ratio is not None:
        lines.append(f"  Тесты: pass_ratio={pass_ratio} ({test_passed}/{test_total})")
    lines.append(f"  История: repair_ok={repaired_ok}, repair_failed={repaired_failed}, accepted_patch={accepted}")
    if history:
        last = history[-1]
        lines.append(
            f"  Последнее событие: {last.get('event_type', 'event')} {last.get('status', 'unknown')} "
            f"path={last.get('target_path', '-') or '-'}"
        )
    lines.append("")
    lines.append("Короткий вывод:")
    lines.append(f"  Что стало лучше: {', '.join(better_parts) if better_parts else 'явного улучшения по истории пока нет'}")
    lines.append(f"  Что ломалось: {', '.join(broken_parts) if broken_parts else 'критичных сбоев в истории не видно'}")
    lines.append(f"  Где риск: {', '.join(risk_parts)}")
    return "\n".join(lines)


def export_quality_status(report_format: str = "text") -> str:
    """Экспортировать quality-отчёт в файл и вернуть путь с кратким итогом."""
    from src.monitoring.metrics import export_quality_report

    result = export_quality_report(report_format=report_format)
    if not result.startswith("Exported to "):
        return result
    path = result.replace("Exported to ", "", 1)
    fmt = (report_format or "text").strip().lower()
    if fmt == "json":
        return f"Quality JSON export готов: {path}"
    if fmt == "full":
        return f"Quality FULL export готов (расширенная история): {path}"
    return f"Quality text export готов: {path}"


def reset_quality_status() -> str:
    """Сбросить quality-метрики и историю событий."""
    from src.monitoring.metrics import metrics

    metrics.reset_quality()
    return "Quality-метрики и история сброшены."


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
            history = quality.get("recent_history") or []
            if history:
                lines.append(f"Quality history: {len(history)} событий [source=runtime_metrics timestamp={ts}]")
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

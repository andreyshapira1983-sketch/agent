"""
Tools для эволюции: обратная связь, опыт, правила промпта, метрики, саморемонт.
"""
from __future__ import annotations

from src.tools.base import tool_schema
from src.tools.registry import register

WORK_ROLES = {
    "analyst",
    "tester",
    "reviewer",
    "developer",
    "researcher",
    "qa",
    "operator",
    "planner",
    "auditor",
    "monitor",
}


def _add_feedback(request: str, response: str, rating: float | None = None) -> str:
    from src.learning.feedback import add_feedback
    add_feedback(request, response, rating)
    return "Feedback recorded."


def _save_experience(task: str, solution: str, success: bool = True) -> str:
    from src.knowledge.store import add_document
    add_document(
        f"Task: {task}\nSolution: {solution}\nSuccess: {success}",
        meta={"type": "experience", "success": success},
    )
    return "Experience saved to knowledge store."


def _update_prompt_rules(new_rule: str) -> str:
    """Добавить правило в config/agent.json (prompt_rules). Меняет поведение агента по опыту."""
    from src.evolution.config_manager import load_config, save_config
    from src.hitl.audit_log import audit
    cfg = load_config()
    current = (cfg.get("prompt_rules") or "").strip()
    updated = f"{current}\n{new_rule}".strip() if current else new_rule
    cfg["prompt_rules"] = updated
    save_config(cfg)
    audit("update_prompt_rules", {"rule_preview": new_rule[:100]})
    try:
        from src.communication.telegram_alerts import send_autonomous_event
        send_autonomous_event("✅ [Автономное действие] Обновлены правила промпта.")
    except Exception:
        pass
    return f"Added rule. Total rules length: {len(updated)} chars."


def _get_metrics() -> str:
    from src.monitoring.metrics import get_metrics
    m = get_metrics()
    parts = [f"Calls: {m.get('calls', 0)}", f"Errors: {m.get('errors', 0)}", f"Successes: {m.get('successes', 0)}"]
    if m.get("last_duration_sec") is not None:
        parts.append(f"Last duration: {m['last_duration_sec']}s")
    if m.get("recent_requests"):
        parts.append("Recent: " + "; ".join(m["recent_requests"][-3:]))
    return " | ".join(parts)


def _analyze_tool_performance(top_n: int = 10) -> str:
    from src.monitoring.metrics import analyze_tool_performance
    return analyze_tool_performance(top_n=top_n)


def _export_performance_summary(file_path: str | None = None) -> str:
    from src.monitoring.metrics import export_performance_summary
    return export_performance_summary(file_path=file_path or None)


def _check_performance_alerts() -> str:
    from src.monitoring.metrics import check_performance_alerts
    return check_performance_alerts()


def _run_self_repair() -> str:
    """Запустить тесты; если упали — предложить правку через sandbox (propose_patch → validate_patch → accept_patch) или откат."""
    from src.tools.orchestrator import run_tool
    out = run_tool("run_pytest", {"path": "tests/"})
    if "Exit code 0" in out or "passed" in out:
        return f"Self-check OK.\n{out[:500]}"
    return (
        f"Tests failed. To fix safely: use propose_patch(path, content) → validate_patch(patch_id) [runs in sandbox] → accept_patch(patch_id). Or rollback_config(version_id).\n{out[:600]}"
    )


def _get_prompt_rules() -> str:
    from src.evolution.config_manager import get_prompt_rules
    r = get_prompt_rules()
    return r or "(no rules yet)"


def _get_feedback_summary(n: int = 20) -> str:
    from src.learning.feedback_analyzer import get_summary_text
    return get_summary_text(n)


def _rollback_config(version_id: str) -> str:
    """Restore config to a previous version. Use get_audit_log to see versions."""
    from src.evolution.versioning import rollback
    from src.evolution.config_manager import save_config
    cfg = rollback(version_id)
    if cfg is None:
        return f"Version {version_id} not found."
    save_config(cfg)
    return f"Rolled back to {version_id}."


def _restore_config_from_template() -> str:
    from src.evolution.config_manager import restore_from_template
    if restore_from_template():
        return "Config restored from agent.json.template."
    return "No config/agent.json.template found."


def _get_audit_log(n: int = 50) -> str:
    from src.hitl.audit_log import format_audit_tail
    return format_audit_tail(n)


def _propose_patch(target_path: str, content: str, reason: str = "") -> str:
    """Submit patch for sandbox validation (self-evolution). Next: validate_patch(patch_id) [runs pytest in sandbox], then accept_patch(patch_id). Safe for self-repair and before create_agent_family."""
    from src.evolution.safety import submit_candidate_patch
    patch_id = submit_candidate_patch(target_path, content, reason)
    return f"Patch submitted: {patch_id}. Next: validate_patch(patch_id) [sandbox], then accept_patch(patch_id). You can then create_agent_family to spawn with evolved code."


def _validate_patch(patch_id: str) -> str:
    """Run tests for candidate patch in sandbox (copy project → apply patch → pytest). If pass, call accept_patch to apply to live code."""
    from src.evolution.safety import validate_candidate_with_tests
    ok = validate_candidate_with_tests(patch_id)
    return "Validation passed in sandbox. You can call accept_patch(patch_id)." if ok else "Validation failed in sandbox (tests failed). Do not accept."


def _accept_patch(patch_id: str) -> str:
    """Apply a validated patch to stable code. Fails if not validated."""
    from src.evolution.safety import accept_patch_to_stable
    return accept_patch_to_stable(patch_id)


def _request_agent_spawn(task_description: str, depth: int = 0) -> str:
    """Request a new agent for a task. Does not create agent — only enqueues. Supervisor creates. Returns request_id or error."""
    from src.agency.agent_spawner import spawn_agent
    req_id = spawn_agent(task_description, depth)
    if req_id is None:
        return "Rejected: max_agents or spawn_depth_limit exceeded. No spawn."
    return f"Spawn requested: {req_id}. Supervisor will process."


def _create_agent_family(role: str, name: str, task_description: str = "") -> str:
    """«Семейка»: создать агента-«ребёнка» с ролью и именем (наследование эмоций). Для эволюции кода перед созданием: propose_patch → validate_patch [sandbox] → accept_patch, затем create_agent_family — дети запускаются с уже обновлённым кодом."""
    from src.agency.agent_spawner import spawn_agent
    from src.agency.supervisor import process_one_spawn_request
    from src.personality.personality import is_neutral_family_mode
    from src.state.agent_state import get_state

    role_clean = (role or "").strip()
    name_clean = (name or "").strip()
    if not role_clean or not name_clean:
        return "Rejected: role and name are required."
    if is_neutral_family_mode() and role_clean.lower() not in WORK_ROLES:
        allowed = ", ".join(sorted(WORK_ROLES))
        return f"Rejected: role '{role_clean}' is not allowed in neutral mode. Allowed roles: {allowed}."

    agent_id = (get_state() or {}).get("agent_id", "root")
    req_id = spawn_agent(
        task_description or f"Agent {name_clean} ({role_clean})",
        depth=1,
        parent_id=agent_id,
        role=role_clean,
        name=name_clean,
    )
    if req_id is None:
        return "Rejected: max_agents or spawn_depth_limit exceeded. No spawn."
    child_id = process_one_spawn_request(start_worker=True, request_id=req_id)
    if child_id:
        return f"Агент '{name_clean}' (роль: {role_clean}) создан: {child_id}. Воркер запущен (наследование эмоций, один цикл)."
    return f"Создание агента '{name_clean}' запрошено: {req_id}. Обработка заявки выполнена."


def _get_my_family_tree() -> str:
    """Дерево «семейки»: я, дети, предки (бабушка, дедушка — по цепочке parent)."""
    from src.agency import supervisor
    from src.state.agent_state import get_state
    agent_id = (get_state() or {}).get("agent_id", "root")
    tree = supervisor.get_family_tree(agent_id)
    self_runtime = (tree.get("self") or {}).get("runtime") or {}
    self_status = self_runtime.get("status") or "unknown"
    lines = [
        f"Я: {tree['self']['name']} (id={tree['self']['id']}, поколение {tree['self']['generation']}, статус {self_status})",
    ]
    if tree.get("children"):
        lines.append(
            "Дети: "
            + ", ".join(
                f"{c['name']} ({c['role']}, status={(c.get('runtime') or {}).get('status', 'unknown')})"
                for c in tree["children"]
            )
        )
    if tree.get("ancestors"):
        lines.append(
            "Предки (родитель → …): "
            + ", ".join(
                f"{a['name']} ({a['role']}, status={(a.get('runtime') or {}).get('status', 'unknown')})"
                for a in tree["ancestors"]
            )
        )
    if not tree.get("children") and not tree.get("ancestors"):
        lines.append("Пока нет детей и предков — только я.")
    return "\n".join(lines)


def _get_my_inbox(clear_after: bool = True) -> str:
    """Прочитать inbox текущего агента (сообщения от других агентов семейки)."""
    from src.state.agent_state import get_state
    from src.agency.family_store import read_inbox
    agent_id = (get_state() or {}).get("agent_id", "root")
    if agent_id == "root":
        return "Inbox только у дочерних агентов."
    messages = read_inbox(agent_id, clear_after=clear_after)
    if not messages:
        return "Inbox пуст."
    lines = [f"[От {m.get('from', '?')}] {m.get('message', '')}" for m in messages]
    return "Сообщения:\n" + "\n".join(lines)


def _send_message_to_agent(to_agent_id: str, message: str) -> str:
    """Отправить сообщение агенту семейки (положит в его inbox)."""
    from src.state.agent_state import get_state
    from src.agency.family_store import append_inbox
    from_agent_id = (get_state() or {}).get("agent_id", "root")
    if not to_agent_id or not message:
        return "Нужны to_agent_id и message."
    append_inbox(to_agent_id, from_agent_id, message[:2000])
    return f"Сообщение отправлено агенту {to_agent_id}."


def _add_professions(professions_input: str) -> str:
    """Добавить профессии в справочник. Ввод: JSON-массив строк или строки через запятую/перенос строки. Семья потом сама может распределить роли и создавать агентов."""
    import json
    from src.agency.professions_store import add_professions, get_professions
    raw = (professions_input or "").strip()
    if not raw:
        return "Нужен список профессий: JSON-массив [\"профессия1\", \"профессия2\"] или строки через запятую/перенос."
    professions: list[str] = []
    if raw.startswith("["):
        try:
            professions = json.loads(raw)
            if not isinstance(professions, list):
                professions = [str(professions)]
            else:
                professions = [str(p).strip() for p in professions if str(p).strip()]
        except Exception:
            for part in raw.replace("\n", ",").split(","):
                if part.strip():
                    professions.append(part.strip())
    else:
        for part in raw.replace("\n", ",").split(","):
            if part.strip():
                professions.append(part.strip())
    added = add_professions(professions)
    total = len(get_professions())
    return f"Добавлено профессий: {added}. Всего в справочнике: {total}. Семья может распределить роли и создавать агентов (create_agent_family) по этим профессиям."


def _get_professions(include_assigned: bool = True) -> str:
    """Список профессий из справочника. Если include_assigned=True — помечает, какие уже заняты членами семьи (дети), какие свободны — по ним можно создавать агентов."""
    from src.agency.professions_store import get_professions, get_assigned_roles
    from src.state.agent_state import get_state
    all_prof = get_professions()
    if not all_prof:
        return "Справочник профессий пуст. Добавь профессии через add_professions (например 5 или 120)."
    if not include_assigned:
        return "Профессии (" + str(len(all_prof)) + "): " + ", ".join(all_prof[:50]) + ("..." if len(all_prof) > 50 else "")
    agent_id = (get_state() or {}).get("agent_id", "root")
    assigned = get_assigned_roles(agent_id)
    lines = [f"Всего профессий: {len(all_prof)}."]
    unassigned = [p for p in all_prof if p not in assigned]
    assigned_list = [p for p in all_prof if p in assigned]
    if assigned_list:
        lines.append("Заняты семьёй: " + ", ".join(assigned_list[:30]) + ("..." if len(assigned_list) > 30 else ""))
    if unassigned:
        lines.append("Свободны (можно создавать агентов): " + ", ".join(unassigned[:30]) + ("..." if len(unassigned) > 30 else ""))
    else:
        lines.append("Все профессии уже распределены по семье.")
    return "\n".join(lines)


def _add_derived_rule(rule: str, score: float = 1.0) -> str:
    """Add a derived rule (learning). Enforces rule_limit. Returns added/rejected."""
    from src.learning.rule_derivation import add_derived_rule as add_rule
    if add_rule(rule, score):
        return "Derived rule added."
    return "Rejected: at rule_limit and rule not better than worst."


def _derive_rules_from_feedback(max_rules: int = 5) -> str:
    """Derive rules from low-rated feedback and add via rules_safety."""
    from src.learning.rule_derivation import derive_rules_from_feedback
    added = derive_rules_from_feedback(max_rules)
    return f"Added {len(added)} derived rules." if added else "No new rules derived (or none passed limit)."


def _get_architecture_docs(max_chars: int = 12000) -> str:
    """Вернуть архитектурные документы из Knowledge Store (долговременная память о себе)."""
    from src.knowledge.store import list_documents
    docs = [d for d in list_documents() if (d.get("meta") or {}).get("type") == "architecture"]
    if not docs:
        return "В хранилище пока нет архитектурных документов. Они добавляются при первом запуске."
    parts = []
    total = 0
    for d in docs:
        if total >= max_chars:
            break
        src_name = (d.get("meta") or {}).get("source", "?")
        content = (d.get("content") or "")[: max_chars - total]
        if len(d.get("content") or "") > len(content):
            content += "..."
        parts.append(f"--- {src_name} ---\n{content}")
        total += len(content)
    return "\n\n".join(parts)


def register_evolution_tools() -> None:
    register(
        "add_feedback",
        tool_schema(
            "add_feedback",
            "Record feedback: request, response, optional rating (e.g. 1.0 good, 0.0 bad). For learning.",
            {
                "request": {"type": "string", "description": "User request or question"},
                "response": {"type": "string", "description": "Agent response"},
                "rating": {"type": "number", "description": "Optional 0-1 rating"},
            },
            required=["request", "response"],
        ),
        _add_feedback,
    )
    register(
        "save_experience",
        tool_schema(
            "save_experience",
            "Save experience to knowledge store: task, solution, success. For self-learning.",
            {
                "task": {"type": "string", "description": "What was the task"},
                "solution": {"type": "string", "description": "What was done"},
                "success": {"type": "boolean", "description": "Whether it worked"},
            },
            required=["task", "solution"],
        ),
        _save_experience,
    )
    register(
        "update_prompt_rules",
        tool_schema(
            "update_prompt_rules",
            "Add a rule to agent prompt (e.g. 'If user asks X, do Y'). Updates config/agent.json.",
            {"new_rule": {"type": "string", "description": "One rule to add"}},
            required=["new_rule"],
        ),
        _update_prompt_rules,
    )
    register(
        "get_metrics",
        tool_schema(
            "get_metrics",
            "Get agent metrics: calls count, errors count.",
            {},
            required=[],
        ),
        _get_metrics,
    )
    register(
        "analyze_tool_performance",
        tool_schema(
            "analyze_tool_performance",
            "Analyze tool execution times: slowest tools by average time, with count and last duration. Use to find performance bottlenecks.",
            {"top_n": {"type": "integer", "description": "Max number of tools to list (default 10)"}},
            required=[],
        ),
        lambda top_n=10: _analyze_tool_performance(top_n=top_n or 10),
    )
    register(
        "export_performance_summary",
        tool_schema(
            "export_performance_summary",
            "Export performance summary (tool_times and fetch cache stats) to a JSON file for historical analysis. Optional file_path; otherwise writes to config/performance_logs/ with timestamp.",
            {"file_path": {"type": "string", "description": "Optional path for output file"}},
            required=[],
        ),
        lambda file_path=None: _export_performance_summary(file_path),
    )
    register(
        "check_performance_alerts",
        tool_schema(
            "check_performance_alerts",
            "Check if any tools exceeded the performance threshold (tool_performance.warn_threshold_sec). Returns a report and writes to audit if alerts found.",
            {},
            required=[],
        ),
        _check_performance_alerts,
    )
    register(
        "run_self_repair",
        tool_schema(
            "run_self_repair",
            "Run self-check (pytest). If tests fail, suggests fix or rollback.",
            {},
            required=[],
        ),
        _run_self_repair,
    )
    register(
        "get_prompt_rules",
        tool_schema(
            "get_prompt_rules",
            "Read current prompt rules from config (for self-improvement engine).",
            {},
            required=[],
        ),
        _get_prompt_rules,
    )
    register(
        "get_feedback_summary",
        tool_schema(
            "get_feedback_summary",
            "Get feedback analyzer summary: good/bad counts, low-rated examples. For learning from errors.",
            {"n": {"type": "integer", "description": "Last n feedback entries (default 20)"}},
            required=[],
        ),
        lambda n=20: _get_feedback_summary(n),
    )
    register(
        "rollback_config",
        tool_schema(
            "rollback_config",
            "Rollback config to a previous version (safety). Version ids: v0, v1, ...",
            {"version_id": {"type": "string", "description": "e.g. v0, v1"}},
            required=["version_id"],
        ),
        _rollback_config,
    )
    register(
        "restore_config_from_template",
        tool_schema(
            "restore_config_from_template",
            "Restore config from config/agent.json.template (self-healing).",
            {},
            required=[],
        ),
        _restore_config_from_template,
    )
    register(
        "get_audit_log",
        tool_schema(
            "get_audit_log",
            "Get recent audit log (file writes, rule updates). For safety review.",
            {"n": {"type": "integer", "description": "Last n entries (default 50)"}},
            required=[],
        ),
        lambda n=50: _get_audit_log(n),
    )
    register(
        "get_architecture_docs",
        tool_schema(
            "get_architecture_docs",
            "Get architecture documents from Knowledge Store (README, ARCHITECTURE_PLAN_FULL, EVOLUTION, GENETICS, SELF_MODEL). Use when you need to re-read your structure, evolution, or genetics.",
            {"max_chars": {"type": "integer", "description": "Max total chars (default 12000)"}},
            required=[],
        ),
        lambda max_chars=12000: _get_architecture_docs(max_chars),
    )
    # Auto-patch: only via sandbox (propose → validate → accept)
    register(
        "propose_patch",
        tool_schema(
            "propose_patch",
            "Self-evolution: submit a code patch for sandbox validation. Next: validate_patch(patch_id) runs pytest in sandbox; then accept_patch(patch_id). Safe before create_agent_family.",
            {
                "target_path": {"type": "string", "description": "Path to file e.g. src/foo/bar.py"},
                "content": {"type": "string", "description": "New file content"},
                "reason": {"type": "string", "description": "Why this patch"},
            },
            required=["target_path", "content"],
        ),
        _propose_patch,
    )
    register(
        "validate_patch",
        tool_schema(
            "validate_patch",
            "Run tests for a candidate patch in sandbox (copy project, apply patch, pytest). If pass, call accept_patch(patch_id).",
            {"patch_id": {"type": "string", "description": "ID from propose_patch"}},
            required=["patch_id"],
        ),
        _validate_patch,
    )
    register(
        "accept_patch",
        tool_schema(
            "accept_patch",
            "Apply a validated patch to stable code. Fails if not validated.",
            {"patch_id": {"type": "string", "description": "ID from propose_patch"}},
            required=["patch_id"],
        ),
        _accept_patch,
    )
    # Agent spawn: only request; Supervisor creates
    register(
        "request_agent_spawn",
        tool_schema(
            "request_agent_spawn",
            "Request a new agent for a task. Does not create — only enqueues. Supervisor creates agents. Respects max_agents and spawn_depth_limit.",
            {
                "task_description": {"type": "string", "description": "What the agent should do"},
                "depth": {"type": "integer", "description": "Spawn depth (default 0)"},
            },
            required=["task_description"],
        ),
        lambda task_description, depth=0: _request_agent_spawn(task_description, depth),
    )
    # Семейка агентов: создать «ребёнка» с ролью/именем, дерево семьи
    register(
        "create_agent_family",
        tool_schema(
            "create_agent_family",
            "Create a «child» agent (family): role and name; emotions inherited. To spawn with evolved code: propose_patch → validate_patch → accept_patch, then create_agent_family.",
            {
                "role": {"type": "string", "description": "Role/gender label: e.g. woman, man, any"},
                "name": {"type": "string", "description": "Agent name"},
                "task_description": {"type": "string", "description": "What the new agent should do (optional)"},
            },
            required=["role", "name"],
        ),
        lambda role, name, task_description="": _create_agent_family(role, name, task_description),
    )
    register(
        "get_my_family_tree",
        tool_schema(
            "get_my_family_tree",
            "Get my family tree: self, children, ancestors (parent, grandparent — бабушка, дедушка).",
            {},
            required=[],
        ),
        _get_my_family_tree,
    )
    register(
        "get_my_inbox",
        tool_schema(
            "get_my_inbox",
            "Read messages from family inbox (for child agents). Returns messages from other agents.",
            {"clear_after": {"type": "boolean", "description": "Clear inbox after reading (default true)"}},
            required=[],
        ),
        lambda clear_after=True: _get_my_inbox(clear_after=clear_after),
    )
    register(
        "send_message_to_agent",
        tool_schema(
            "send_message_to_agent",
            "Send a message to another agent in the family (adds to their inbox).",
            {
                "to_agent_id": {"type": "string", "description": "Agent id (e.g. from get_my_family_tree children)"},
                "message": {"type": "string", "description": "Message text"},
            },
            required=["to_agent_id", "message"],
        ),
        lambda to_agent_id, message: _send_message_to_agent(to_agent_id, message),
    )
    # Справочник профессий: добавить 5, 120 или сколько угодно — семья сама распределяет роли и может создавать агентов
    register(
        "add_professions",
        tool_schema(
            "add_professions",
            "Add professions to the reference list. Input: JSON array [\"profession1\", \"profession2\"] or comma/newline separated. Family can then assign roles and create agents (create_agent_family) for unassigned professions.",
            {"professions_input": {"type": "string", "description": "JSON array of profession names or comma/newline separated list"}},
            required=["professions_input"],
        ),
        _add_professions,
    )
    register(
        "get_professions",
        tool_schema(
            "get_professions",
            "List professions from reference. With include_assigned=True shows which are already taken by family members and which are free — create agents for free ones with create_agent_family.",
            {"include_assigned": {"type": "boolean", "description": "Show which professions are assigned to family (default true)"}},
            required=[],
        ),
        lambda include_assigned=True: _get_professions(include_assigned=include_assigned),
    )
    # Rule derivation: only via rules_safety (limit, score, decay)
    register(
        "add_derived_rule",
        tool_schema(
            "add_derived_rule",
            "Add a derived rule (learning). Enforces rule_limit and rule_decay.",
            {
                "rule": {"type": "string", "description": "Rule text"},
                "score": {"type": "number", "description": "Score 0-1 (default 1.0)"},
            },
            required=["rule"],
        ),
        lambda rule, score=1.0: _add_derived_rule(rule, score),
    )
    register(
        "derive_rules_from_feedback",
        tool_schema(
            "derive_rules_from_feedback",
            "Derive rules from low-rated feedback and add via rules_safety (limit applied).",
            {"max_rules": {"type": "integer", "description": "Max rules to derive (default 5)"}},
            required=[],
        ),
        lambda max_rules=5: _derive_rules_from_feedback(max_rules),
    )

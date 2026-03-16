"""
Self-improvement planner: takes analyze_self_model output, forms improvement list,
ranks by importance, proposes to user or plans next steps.
"""
from __future__ import annotations

from typing import Any

from src.reflection.self_model_analyzer import analyze


def plan_improvements(analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Form improvement plan from self-model analysis.
    Returns: improvements (list of {priority, action, reason}), summary_text.
    """
    if analysis is None:
        analysis = analyze()
    missing = analysis.get("missing") or []
    can_improve = analysis.get("can_improve") or []
    improvements: list[dict[str, Any]] = []

    # Rank missing by priority: evolution/core first, then memory/learning, then rest
    priority_order = ("evolution", "core", "reflection", "learning", "memory", "security", "monitoring", "tools")
    for item in missing:
        if item.startswith("capability."):
            name = item.replace("capability.", "")
            if name in ("vector_memory", "chroma_or_vector_store"):
                improvements.append({"priority": 1, "action": f"Add {name}", "reason": "Enables semantic memory and retrieval."})
            elif name in ("self_improvement_planner", "reflection_engine"):
                improvements.append({"priority": 2, "action": f"Strengthen {name}", "reason": "Already present as module; extend logic."})
            elif name == "agent_spawner":
                improvements.append({
                    "priority": 3,
                    "action": "Add agent_spawner",
                    "reason": "Use agency.supervisor.request_spawn only (no direct create). Templates + create_from_template + run_pytest.",
                })
            else:
                improvements.append({"priority": 4, "action": f"Consider {name}", "reason": "In reference capabilities."})
        else:
            parts = item.split(".", 1)
            sys_name = parts[0] if len(parts) > 1 else ""
            p = next((i for i, x in enumerate(priority_order) if x == sys_name), 5)
            improvements.append({"priority": p, "action": f"Add module {item}", "reason": f"Missing from reference ({sys_name})."})

    for c in can_improve:
        improvements.append({"priority": 2, "action": "Reach capability level", "reason": c})

    # Sort by priority
    improvements.sort(key=lambda x: (x["priority"], x["action"]))

    summary_lines = [
        "=== Improvement plan ===",
        f"Total suggestions: {len(improvements)}",
        "",
        "Top items:",
    ]
    for i, imp in enumerate(improvements[:15], 1):
        summary_lines.append(f"  {i}. [P{imp['priority']}] {imp['action']} — {imp['reason']}")

    return {
        "improvements": improvements,
        "summary_text": "\n".join(summary_lines),
        "count": len(improvements),
    }


def get_improvement_plan_text() -> str:
    from typing import cast
    return cast(str, plan_improvements()["summary_text"])

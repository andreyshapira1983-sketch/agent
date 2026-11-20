"""Question classification and governing-doc routing for the planner."""
from __future__ import annotations

from typing import Any


def _keeps_step_references(fn: Any) -> Any:
    """Reordering plan steps also renumbers the {{step:N.output}} references."""
    import functools

    from core.step_references import renumber_step_references

    @functools.wraps(fn)
    def wrapper(sources: list[dict[str, Any]], *args: Any, **kwargs: Any) -> Any:
        before = list(sources)
        return renumber_step_references(before, fn(sources, *args, **kwargs))

    return wrapper

BROAD_PROJECT_CONTEXT_TERMS = (
    "your project",
    "this project",
    "our project",
    "my project",
    "current project",
    "project status",
    "project state",
    "project overview",
    "repo",
    "repository",
    "codebase",
    "своем проект",
    "своём проект",
    "твоем проект",
    "твоём проект",
    "нашем проект",
    "этом проект",
    "о проект",
    "про проект",
    "статус проект",
    "состояние проект",
)
BROAD_PROJECT_QUESTION_TERMS = (
    "what do you know",
    "already know",
    "tell me about",
    "summarize",
    "summary",
    "overview",
    "status",
    "state",
    "что ты",
    "что уже",
    "знаешь",
    "расскажи",
    "сводка",
    "обзор",
    "статус",
    "состояние",
)
_ARCHITECTURE_REFERENCE_TERMS = (
    "architecture",
    "architectural",
    "design",
    "reference",
    "overview architecture",
    "архитектура",
    "архитектуре",
    "архитектур",
    "дизайн",
)
_PROJECT_MEMORY_TAG_TERMS = (
    "tags: project",
    "tags: bug",
    "tags: memory",
    "tags: budget",
    "tags: operator-routing",
    "tags: patch-proposal",
    "tags: tech debt",
    "tags: autonomy",
    "tags: tests",
    "tags: model",
    "tags: status",
    "tech_debt.md",
    "tech-debt.md",
)


DOCTRINE_CORPORATE_STRONG_TERMS = (
    "corporate model",
    "central agent governance",
    "central agent",
    "safe autonomy",
    "night observation",
)
DOCTRINE_CORPORATE_CONTEXTUAL_TERMS = (
    "doctrine",
    "корпоратив",
    "центральн",
    "доктрин",
)
DOCTRINE_CORPORATE_TOPIC_TERMS = (
    "corporate",
    "governance",
    "subagent",
    "sub-agent",
    "self-build",
    "self build",
    "night",
    "observation",
    "safe autonomy",
    "autonomy",
    "корпоратив",
    "управлен",
    "субагент",
    "сабагент",
    "самосбор",
    "ночн",
    "наблюден",
    "автоном",
)
DOCTRINE_CORPORATE_CONTEXT_TERMS = (
    "agent",
    "project",
    "repo",
    "repository",
    "architecture",
    "roadmap",
    "агент",
    "проект",
    "репозитор",
    "архитектур",
)
DOCTRINE_CORPORATE_DOC_PATHS = (
    "knowledge/doctrine/future/CORPORATE_MODEL.md",
    "knowledge/doctrine/CENTRAL_AGENT_GOVERNANCE.md",
    "knowledge/generated/AGENT_ANATOMY.md",
    "knowledge/doctrine/ROADMAP.md",
    "knowledge/maps/COMMANDS_MAP.md",
)
# Conditional doc group for sub-agent questions (delegation, role trust, quarantine, lifecycle); kept out of the universal manifest so unrelated questions do not read it. SUBAGENT_LIFECYCLE.md is the normative contract.
_SUBAGENT_GOVERNANCE_DOC_PATHS = (
    "knowledge/doctrine/SUBAGENT_LIFECYCLE.md",
)
# Conditional doc group for memory / durable-learning questions; never in the universal manifest. Membership is small: only documents whose rules hold in the code today.
_MEMORY_GOVERNANCE_DOC_PATHS = (
    # Do not list a path that no longer exists: the router injects a file_read per path without checking, and a missing file raises FileNotFoundError.
    "knowledge/doctrine/MEMORY_SYSTEM_AUDIT.md",
    "knowledge/doctrine/self-audit-lessons.md",
)
# Conditional doc group for self-diagnosis / self-repair reasoning (protocol, not history; the lessons file already belongs to the memory group); kept out of the universal manifest.
_SELF_REPAIR_DOCTRINE_DOC_PATHS = (
    "knowledge/doctrine/SELF_REPAIR_DOCTRINE.md",
)
_DOCTRINE_LOW_SIGNAL_DEFAULT_PATHS = (
    "README.md",
    "core/planner.py",
    "core/loop.py",
    "core/autonomous_runtime.py",
    "core/self_repair.py",
    "core/smart_memory.py",
)
_IMPLEMENTATION_DETAIL_TERMS = (
    "implemented",
    "implementation",
    "working",
    "code",
    "source",
    "real gaps",
    "critique",
    "broken",
    "bug",
    "уже сделано",
    "реализовано",
    "работает",
    "код",
    "исходн",
    "проверь код",
    "крити",
    "сломано",
    "баг",
)
CONFIDENCE_EVIDENCE_DIAGNOSTIC_TERMS = (
    # Operator wording, not module names; `confidence` / `low_confidence_gate` are the old vocabulary kept so old phrasing still routes here (live module: core/evidence_support.py).
    "confidence",
    "low-confidence",
    "low confidence",
    "low_confidence",
    "low_confidence_gate",
    "confidence gate",
    "evidence_support",
    "evidence support",
    "evidence_support_score",
    "evidence_score",
    "overall_confidence",
    "citation",
    "citations",
    "verifier",
    "verified",
    "unverified",
    "source registry",
    "source_registry",
    "цитат",
    "вериф",
)
CONFIDENCE_EVIDENCE_SOURCE_PATHS = (
    "core/verifier.py",
    "tests/test_verifier.py",
    "tests/test_evidence_support.py",
    "tests/test_confidence_vector.py",
)
# Low-signal defaults a drafted plan falls back to without a real evidence source; _is_low_signal_confidence_source must read this tuple, not re-encode the values.
_CONFIDENCE_LOW_SIGNAL_DEFAULT_PATHS = (
    "README.md",
    "tools/",
)

def is_broad_project_self_knowledge_question(question: str) -> bool:
    lowered = (question or "").casefold()
    return (
        any(term in lowered for term in BROAD_PROJECT_CONTEXT_TERMS)
        and any(term in lowered for term in BROAD_PROJECT_QUESTION_TERMS)
    )


def _explicitly_requests_readme(question: str) -> bool:
    lowered = (question or "").casefold()
    return "readme" in lowered or "readme.md" in lowered


def _explicitly_requests_architecture_reference(question: str) -> bool:
    lowered = (question or "").casefold()
    return any(term in lowered for term in _ARCHITECTURE_REFERENCE_TERMS)


def _history_has_project_status_memory(history: str) -> bool:
    lowered = (history or "").casefold()
    return "<long_term_memory>" in lowered and any(
        term in lowered for term in _PROJECT_MEMORY_TAG_TERMS
    )


def _should_prefer_memory_over_readme(question: str, history: str) -> bool:
    return (
        is_broad_project_self_knowledge_question(question)
        and _history_has_project_status_memory(history)
        and not _explicitly_requests_readme(question)
        and not _explicitly_requests_architecture_reference(question)
    )


def is_doctrine_corporate_question(question: str) -> bool:
    lowered = (question or "").casefold()
    if any(term in lowered for term in DOCTRINE_CORPORATE_STRONG_TERMS):
        return True
    has_context = any(
        term in lowered for term in DOCTRINE_CORPORATE_CONTEXT_TERMS
    )
    if (
        has_context
        and any(term in lowered for term in DOCTRINE_CORPORATE_CONTEXTUAL_TERMS)
    ):
        return True
    topic_hits = {
        term for term in DOCTRINE_CORPORATE_TOPIC_TERMS
        if term in lowered
    }
    return len(topic_hits) >= 2 and has_context


# Two-tier: one strong term is enough; weaker action terms count only with an agent/role context word ("pause the build" must not pull in the sub-agent doc).
_SUBAGENT_DOC_STRONG_TERMS = (
    "subagent",
    "sub-agent",
    "sub agent",
    "spawn_subagent",
    "team executor",
    "team_executor",
    "delegation",
    "субагент",
    "сабагент",
    "подагент",
    "делегир",
)
_SUBAGENT_DOC_CONTEXT_TERMS = (
    "agent",
    "агент",
    "role",
    "роль",
)
_SUBAGENT_DOC_ACTION_TERMS = (
    "quarantine",
    "карантин",
    "pause",
    "paused",
    "pausing",
    "пауз",
    "приостанов",
    "retire",
    "retirement",
    "requalif",
    "trust boundary",
    "довери",
    "lifecycle",
    "жизненн",
    "уволить",
)


def _is_subagent_governance_question(question: str) -> bool:
    lowered = (question or "").casefold()
    if any(term in lowered for term in _SUBAGENT_DOC_STRONG_TERMS):
        return True
    has_context = any(term in lowered for term in _SUBAGENT_DOC_CONTEXT_TERMS)
    has_action = any(term in lowered for term in _SUBAGENT_DOC_ACTION_TERMS)
    return has_context and has_action


# Two-tier like the sub-agent detector: plain substring "memory" would fire on "not enough memory"; each list has paired negative tests.
_MEMORY_DOC_STRONG_TERMS = (
    "episodic",
    "эпизодическ",
    "procedural memory",
    "процедурная память",
    "процедурной памяти",
    "semantic memory",
    "семантическая память",
    "working memory",
    "рабочая память",
    "persistent memory",
    "долговременная память",
    "smart_memory",
    "persistent_memory",
    "episodic_memory",
    "procedural_memory",
    "memory governance",
    "memory policy",
    "политика памяти",
    "memory hygiene",
    "гигиена памяти",
    "consolidation",
    "консолидац",
    "forgetting",
    "забывани",
)
_MEMORY_DOC_CONTEXT_TERMS = (
    "memory",
    "память",
    "памяти",
    "памятью",
)
_MEMORY_DOC_ACTION_TERMS = (
    "retrieval",
    "retrieve",
    "извлечен",
    "store",
    "хранилищ",
    "prune",
    "pruning",
    "очист",
    "provenance",
    "провенанс",
    "trust",
    "довери",
    "learn",
    # Both stems ("обучение/обученный", "обучается/обучаться"); used only with a memory context word.
    "обучен",
    "обуча",
    "governance",
)


def _is_memory_governance_question(question: str) -> bool:
    lowered = (question or "").casefold()
    if any(term in lowered for term in _MEMORY_DOC_STRONG_TERMS):
        return True
    has_context = any(term in lowered for term in _MEMORY_DOC_CONTEXT_TERMS)
    has_action = any(term in lowered for term in _MEMORY_DOC_ACTION_TERMS)
    return has_context and has_action


# Two-tier: the strong list holds only vocabulary about the repair protocol itself; ordinary repair words need an action word, so "fix the bug in core/loop.py" stays an ordinary task.
_SELF_REPAIR_DOC_STRONG_TERMS = (
    "self-repair",
    "self repair",
    "self_repair",
    "self-diagnos",
    "self diagnos",
    "самовосстановлен",
    "саморемонт",
    "самодиагност",
    "root cause",
    "root-cause",
    "первопричин",
    "корневая причина",
    "корневой причин",
    "корневую причину",
    "fail-before",
    "fail before test",
    "regression test",
    "regression guard",
    "регрессионн",
    "backfill",
    "бэкфилл",
    "бекфилл",
    "post-mortem",
    "postmortem",
    "idempot",
    "идемпотент",
)
_SELF_REPAIR_DOC_CONTEXT_TERMS = (
    "repair",
    "ремонт",
    "починк",
    "чинит",
    "чинить",
    "fix",
    "исправ",
    "bug",
    "баг",
    "defect",
    "дефект",
    "regression",
    "регресс",
    "migration",
    "миграц",
    "breakage",
    "поломк",
    "сломал",
    "rollback",
    "откат",
    "self-build",
    "self build",
    "self_build",
    "самосбор",
)
_SELF_REPAIR_DOC_ACTION_TERMS = (
    "protocol",
    "протокол",
    "doctrine",
    "доктрин",
    "diagnos",
    "диагност",
    "investigate",
    "расслед",
    "cause",
    "причин",
    "invariant",
    "инвариант",
    "safely",
    "безопасн",
    "procedure",
    "процедур",
    "dry-run",
    "dry run",
    "сухой прогон",
    "backup",
    "бэкап",
    "резервн",
    "reproduce",
    "воспроизв",
)


def _is_self_repair_doctrine_question(question: str) -> bool:
    lowered = (question or "").casefold()
    if any(term in lowered for term in _SELF_REPAIR_DOC_STRONG_TERMS):
        return True
    # A task over named files is not a question about the repair protocol ("исправь x.md ... резервная копия" matched "исправ" + "резервн").
    from core.file_request_intent import extract_path_mentions
    if extract_path_mentions(question or ""):
        return False
    has_context = any(term in lowered for term in _SELF_REPAIR_DOC_CONTEXT_TERMS)
    has_action = any(term in lowered for term in _SELF_REPAIR_DOC_ACTION_TERMS)
    return has_context and has_action


def confidence_sources_present(root: Any) -> bool:
    """Whether the workspace has the files the verifier route says to read; outside the own repository the route stays silent, an unknown root keeps the old behaviour."""
    if root is None:
        return True
    from pathlib import Path

    return any((Path(root) / path).is_file() for path in CONFIDENCE_EVIDENCE_SOURCE_PATHS)


def doctrine_docs_present(root: Any) -> bool:
    """Whether the workspace has at least one document the doctrine, sub-agent, memory and self-repair routes tell to read."""
    if root is None:
        return True
    from pathlib import Path

    paths = (DOCTRINE_CORPORATE_DOC_PATHS + _SUBAGENT_GOVERNANCE_DOC_PATHS
             + _MEMORY_GOVERNANCE_DOC_PATHS + _SELF_REPAIR_DOCTRINE_DOC_PATHS)
    return any((Path(root) / path).is_file() for path in paths)


def is_confidence_evidence_diagnostic_question(question: str) -> bool:
    lowered = (question or "").casefold()
    # A task over named files is not a question about citation diagnostics.
    from core.file_request_intent import extract_path_mentions
    if extract_path_mentions(question or ""):
        return False
    return any(term in lowered for term in CONFIDENCE_EVIDENCE_DIAGNOSTIC_TERMS)


def _requests_implementation_detail(question: str) -> bool:
    lowered = (question or "").casefold()
    return any(term in lowered for term in _IMPLEMENTATION_DETAIL_TERMS)


def _norm_source_path(path: str) -> str:
    return path.strip().casefold().replace("\\", "/")





def _file_read_source_spec(path: str, why: str) -> dict[str, Any]:
    """Read step appended by a routing RULE, not by the model; the rationale names the rule so the reasoning check does not blame the agent for steps it did not choose."""
    return {
        "tool": "file_read",
        "arguments": {"path": path},
        "label": f"file:{path}",
        "expected_outcome": "Non-empty UTF-8 text from the requested local source.",
        "rationale": f"routing rule: {why}",
    }


def _is_low_signal_confidence_source(src: dict[str, Any]) -> bool:
    args = src.get("arguments") or {}
    path = args.get("path") if isinstance(args, dict) else None
    norm = _norm_source_path(path) if isinstance(path, str) else ""
    tool = src.get("tool")
    for default in _CONFIDENCE_LOW_SIGNAL_DEFAULT_PATHS:
        norm_default = _norm_source_path(default)
        target = norm_default.rstrip("/")
        if norm_default.endswith("/"):
            if tool == "list_dir" and norm.rstrip("/") == target:
                return True
        elif tool == "file_read" and norm == target:
            return True
    return False


@_keeps_step_references
def _ensure_confidence_evidence_sources_first(
    sources: list[dict[str, Any]],
    warnings: list[str],
    *,
    drop_low_signal_defaults: bool,
) -> list[dict[str, Any]]:
    required_by_norm = {
        _norm_source_path(path): path for path in CONFIDENCE_EVIDENCE_SOURCE_PATHS
    }
    existing_required: dict[str, dict[str, Any]] = {}
    remainder: list[dict[str, Any]] = []
    dropped: list[str] = []

    for src in sources:
        args = src.get("arguments") or {}
        path = args.get("path") if isinstance(args, dict) else None
        norm = _norm_source_path(path) if isinstance(path, str) else ""
        if src.get("tool") == "file_read" and norm in required_by_norm:
            existing_required[norm] = src
            continue
        if drop_low_signal_defaults and _is_low_signal_confidence_source(src):
            dropped.append(path if isinstance(path, str) else str(src.get("tool")))
            continue
        remainder.append(src)

    ordered: list[dict[str, Any]] = []
    injected: list[str] = []
    for path in CONFIDENCE_EVIDENCE_SOURCE_PATHS:
        norm = _norm_source_path(path)
        existing = existing_required.get(norm)
        if existing is not None:
            ordered.append(existing)
        else:
            ordered.append(_file_read_source_spec(
                path, "confidence/evidence question, the verifier's own sources are read first"))
            injected.append(path)

    if injected:
        warnings.append(
            "confidence/evidence verifier sources injected before generic sources: "
            + ", ".join(injected)
        )
    if dropped:
        warnings.append(
            "confidence/evidence source selection dropped low-signal defaults: "
            + ", ".join(dropped)
        )
    return ordered + remainder


@_keeps_step_references
def _ensure_doctrine_docs_first(
    sources: list[dict[str, Any]],
    warnings: list[str],
    *,
    drop_default_code_sources: bool,
) -> list[dict[str, Any]]:
    docs_by_norm = {_norm_source_path(path): path for path in DOCTRINE_CORPORATE_DOC_PATHS}
    default_drop = {
        _norm_source_path(path) for path in _DOCTRINE_LOW_SIGNAL_DEFAULT_PATHS
    }

    existing_docs: dict[str, dict[str, Any]] = {}
    remainder: list[dict[str, Any]] = []
    dropped_paths: list[str] = []
    for src in sources:
        args = src.get("arguments") or {}
        path = args.get("path") if isinstance(args, dict) else None
        norm = _norm_source_path(path) if isinstance(path, str) else ""
        if src.get("tool") == "file_read" and norm in docs_by_norm:
            existing_docs[norm] = src
            continue
        if (
            drop_default_code_sources
            and src.get("tool") == "file_read"
            and norm in default_drop
        ):
            dropped_paths.append(path)
            continue
        remainder.append(src)

    ordered_docs: list[dict[str, Any]] = []
    injected: list[str] = []
    for path in DOCTRINE_CORPORATE_DOC_PATHS:
        norm = _norm_source_path(path)
        existing = existing_docs.get(norm)
        if existing is not None:
            ordered_docs.append(existing)
        else:
            ordered_docs.append(_file_read_source_spec(
                path, "question about the agent itself, its doctrine documents are read first"))
            injected.append(path)

    if injected:
        warnings.append(
            "doctrine/corporate docs injected before code sources: "
            + ", ".join(injected)
        )
    if dropped_paths:
        warnings.append(
            "doctrine/corporate source selection dropped low-signal defaults: "
            + ", ".join(dropped_paths)
        )
    return ordered_docs + remainder


@_keeps_step_references
def _ensure_thematic_docs_first(
    sources: list[dict[str, Any]],
    warnings: list[str],
    *,
    target_paths: tuple[str, ...],
    lead_paths: tuple[str, ...],
    warning_prefix: str,
) -> list[dict[str, Any]]:
    """Place one thematic doc group ahead of code, behind higher-priority groups; the single implementation of the ordering and de-duplication rule for all conditional groups."""
    target_norms = {_norm_source_path(path) for path in target_paths}
    lead_norms = {_norm_source_path(path) for path in lead_paths}

    found: dict[str, dict[str, Any]] = {}
    rest: list[dict[str, Any]] = []
    for src in sources:
        args = src.get("arguments") or {}
        path = args.get("path") if isinstance(args, dict) else None
        norm = _norm_source_path(path) if isinstance(path, str) else ""
        if src.get("tool") == "file_read" and norm in target_norms:
            found.setdefault(norm, src)
            continue
        rest.append(src)

    insert_at = 0
    for src in rest:
        args = src.get("arguments") or {}
        path = args.get("path") if isinstance(args, dict) else None
        norm = _norm_source_path(path) if isinstance(path, str) else ""
        if src.get("tool") == "file_read" and norm in lead_norms:
            insert_at += 1
        else:
            break

    ordered_target: list[dict[str, Any]] = []
    injected: list[str] = []
    for path in target_paths:
        norm = _norm_source_path(path)
        existing = found.get(norm)
        if existing is not None:
            ordered_target.append(existing)
        else:
            ordered_target.append(_file_read_source_spec(path, warning_prefix.strip().rstrip(":")))
            injected.append(path)

    if injected:
        warnings.append(warning_prefix + ", ".join(injected))
    return rest[:insert_at] + ordered_target + rest[insert_at:]


def _ensure_subagent_governance_docs_first(
    sources: list[dict[str, Any]],
    warnings: list[str],
) -> list[dict[str, Any]]:
    """Ensure the thematic sub-agent lifecycle doc leads a sub-agent question."""
    return _ensure_thematic_docs_first(
        sources,
        warnings,
        target_paths=_SUBAGENT_GOVERNANCE_DOC_PATHS,
        lead_paths=DOCTRINE_CORPORATE_DOC_PATHS,
        warning_prefix="subagent governance docs injected for a sub-agent question: ",
    )


def _ensure_memory_governance_docs_first(
    sources: list[dict[str, Any]],
    warnings: list[str],
) -> list[dict[str, Any]]:
    """Ensure the thematic memory docs lead a memory/durable-learning question."""
    return _ensure_thematic_docs_first(
        sources,
        warnings,
        target_paths=_MEMORY_GOVERNANCE_DOC_PATHS,
        lead_paths=DOCTRINE_CORPORATE_DOC_PATHS + _SUBAGENT_GOVERNANCE_DOC_PATHS,
        warning_prefix="memory governance docs injected for a memory question: ",
    )


def _ensure_self_repair_doctrine_docs_first(
    sources: list[dict[str, Any]],
    warnings: list[str],
) -> list[dict[str, Any]]:
    """Ensure the self-repair protocol leads a self-diagnosis/repair question."""
    return _ensure_thematic_docs_first(
        sources,
        warnings,
        target_paths=_SELF_REPAIR_DOCTRINE_DOC_PATHS,
        lead_paths=(
            DOCTRINE_CORPORATE_DOC_PATHS
            + _SUBAGENT_GOVERNANCE_DOC_PATHS
            + _MEMORY_GOVERNANCE_DOC_PATHS
        ),
        warning_prefix=(
            "self-repair doctrine docs injected for a self-repair question: "
        ),
    )


@_keeps_step_references
def _drop_readme_status_sources(
    sources: list[dict[str, Any]],
    warnings: list[str],
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for src in sources:
        args = src.get("arguments") or {}
        path = args.get("path") if isinstance(args, dict) else None
        is_readme_file_read = (
            src.get("tool") == "file_read"
            and isinstance(path, str)
            and path.strip().casefold().replace("\\", "/") == "readme.md"
        )
        if is_readme_file_read:
            warnings.append(
                "file_read README.md dropped for broad project status because "
                "fresh long_term_memory is available"
            )
            continue
        filtered.append(src)
    return filtered

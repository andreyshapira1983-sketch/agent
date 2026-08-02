"""Question classification and governing-doc routing for the planner.

Which questions are about the project itself, which demand the doctrine,
memory-governance, subagent or self-repair documents, and how those document
sources are injected ahead of (or dropped from) a drafted plan. Every function
here is pure text analysis over the question/history/sources -- none touches
planner state or the LLM. Moved byte-verbatim from core/planner.py, where this
family was ~940 lines between the module header and the system prompt.
"""
from __future__ import annotations

from typing import Any

from core.lang_match import any_term_matches, normalize_text, tokenize


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
# Signals that a question is introspection about the agent's OWN private repo /
# self — code, architecture, memory, sub-agents, its own PRs. The public web
# cannot answer these; a web_search here only returns generic noise (e.g. a CNN
# homepage) that then pollutes the source registry. See _drop_web_lookup_for_introspection.
_SELF_REPO_INTROSPECTION_TERMS = (
    # English
    "your repository", "your repo", "your codebase", "your code",
    "your architecture", "your own architecture", "your memory",
    "your long-term memory", "your subagent", "your sub-agent",
    "your subagents", "your sub-agents", "your behavior", "your behaviour",
    "your own behavior", "your own behaviour", "yourself", "self-repair",
    "self repair", "your weaknesses", "your own weaknesses", "your tests",
    # Russian (stemmed to survive inflection)
    "свой репозитор", "своё репозитор", "своем репозитор", "своём репозитор",
    "твой репозитор", "твоем репозитор", "твоём репозитор",
    "своей архитектур", "свою архитектур", "своего кода", "твоей архитектур",
    "своей памяти", "твоей памяти", "долговременной памяти", "долговременную память",
    "своём поведени", "своем поведени", "твоём поведени", "твоем поведени",
    "своего поведени", "твоего поведени", "субагент", "суб-агент",
    "своих слабых мест", "свои слабые места", "своей работе", "своей работы",
    "в собственной работе", "собственной архитектур",
)
# Negative guard: signals the question genuinely wants outside / current / web
# information or a comparison against a named external system. When present, the
# web egress is legitimate and must NOT be dropped even if a self-repo term also
# appears (e.g. "compare your architecture with AutoGen").
_EXTERNAL_LOOKUP_TERMS = (
    "http://", "https://", "www.",
    "latest news", "current news", "on the web", "on the internet",
    "search the web", "search online", "look it up", "look up online",
    "autogen", "metagpt", "langchain", "langgraph", "crewai", "autogpt",
    "arxiv", "research paper", "papers on", "state of the art", "state-of-the-art",
    # Russian
    "в интернете", "в сети интернет", "в вебе", "в вэбе",
    "последние новости", "поиск в интернете", "на рынке", "в открытых источник",
    "научн", "статью", "статьи про",
)
# Comparison verbs ("compare", "сравни") do NOT by themselves imply an external
# lookup — you can compare the agent against its OWN past state ("сравни своё
# поведение с состоянием до этих PR"). A genuine *external* comparison is
# signalled by the comparison TARGET (a named framework above, a URL, or a web
# phrase). Treating a bare comparison verb as external caused a real regression:
# the substring "сравни с" matched inside "сравни своё…", so a purely
# introspective question was mis-flagged as external and web_search was allowed
# to run, polluting the source registry. See _wants_external_lookup.

# --- Russian morphological rule for self-repo introspection -------------------
# A hand-written stem list cannot keep up with Russian possessive-pronoun
# inflection (свой/своя/своё/свои/своего/своей/своих/своим/своём/свою, and the
# твой-/собственн- families). Instead of enumerating every pronoun+noun phrase,
# the Russian rule detects a self-referential pronoun (a *closed* inflection
# set, matched as whole tokens after ё->е normalization) co-occurring with a
# self-domain noun *stem*. This is boundary-correct and inflection-robust.
_RU_SELF_PRONOUNS = frozenset(
    normalize_text(w)
    for w in (
        "свой", "своя", "своё", "свое", "свои", "своего", "своей", "своих",
        "своим", "своими", "своём", "своем", "свою",
        "твой", "твоя", "твоё", "твое", "твои", "твоего", "твоей", "твоих",
        "твоим", "твоими", "твоём", "твоем", "твою",
        "себя", "себе", "собой",
        "собственный", "собственная", "собственное", "собственные",
        "собственного", "собственной", "собственных", "собственную",
        "собственным", "собственными", "собственном",
    )
)
# Self-domain noun stems (prefix-matched on token starts, so all inflections of
# репозиторий/архитектура/память/поведение/… are covered).
_RU_SELF_DOMAIN_STEMS = tuple(
    normalize_text(s)
    for s in (
        "репозитор", "архитектур", "памят", "поведени", "субагент",
        "планировщик", "верификатор", "код", "кодбейз", "модул", "слаб",
        "тест", "самовосстановл", "реестр", "эпизод", "процедурн", "навык",
    )
)


def _ru_pronoun_domain_introspection(tokens: tuple[str, ...]) -> bool:
    """True when a self-domain noun stem immediately follows a Russian
    self-referential pronoun (e.g. "свою архитектуру", "свой репозиторий",
    "своими тестами").

    Adjacency — not mere co-occurrence — is required on purpose. Co-occurrence
    anywhere in the sentence is far too broad: "в своей стране архитектура
    власти" or "в своём городе сдают тесты" would be mis-flagged as self-repo
    introspection and wrongly denied a legitimate web lookup. The pronoun must
    directly modify the domain noun, which in Russian means it sits right before
    it. Stable collocations that place an adjective between the pronoun and the
    noun (e.g. "твоей долговременной памяти") are handled by the phrase list, so
    they do not need a wider window here."""
    for i, tok in enumerate(tokens):
        if tok not in _RU_SELF_PRONOUNS:
            continue
        if i + 1 < len(tokens):
            nxt = tokens[i + 1]
            if any(nxt.startswith(stem) for stem in _RU_SELF_DOMAIN_STEMS):
                return True
    return False


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
    "docs/future/CORPORATE_MODEL.md",
    "docs/CENTRAL_AGENT_GOVERNANCE.md",
    "docs/AGENT_ANATOMY.md",
    "docs/ROADMAP.md",
    "docs/COMMANDS_MAP.md",
)
# Thematic (conditional) doc group. Unlike the corporate manifest above, this is
# NOT injected on every doctrine question — only when the question is actually
# about sub-agents / delegation / team execution / role trust / quarantine /
# pause / retire / lifecycle. Keeping it out of the universal manifest stops the
# central agent from reading a growing pile of files on unrelated architecture
# questions. SUBAGENT_LIFECYCLE.md is the normative sub-agent lifecycle contract.
_SUBAGENT_GOVERNANCE_DOC_PATHS = (
    "docs/SUBAGENT_LIFECYCLE.md",
)
# Thematic (conditional) doc group for memory / durable-learning questions,
# same discipline as the sub-agent group above: never in the universal
# manifest, so unrelated architecture turns do not pay for it.
#
# Until this existed the agent could not read its own memory problem history
# (MEMORY_FIX_PLAN B.3), which is failure mode OFM-015 — a record written and
# never read changes nothing.
#
# Membership is deliberately small, and three obvious candidates are left OUT:
#   * docs/audit/MEMORY_LIFECYCLE_CONTRACT.md — v2-draft, never approved, no
#     code implements it. Injecting it as doctrine would teach the agent that
#     unimplemented rules are current behaviour.
#   * docs/MEMORY_FIX_PLAN.md — partly superseded; its A3 prescription was
#     never applied as written, so it would teach a rule that does not hold.
#   * docs/audit/MASTER_ISSUE_REGISTRY.md — the authoritative status owner, but
#     far too large to inject per turn. Reach it by name when a status is
#     actually needed.
_MEMORY_GOVERNANCE_DOC_PATHS = (
    "docs/audit/MEMORY_MAP.md",
    "docs/MEMORY_SYSTEM_AUDIT.md",
    "docs/self-audit-lessons.md",
)
# Thematic (conditional) doc group for self-diagnosis / self-repair reasoning:
# how to prove a defect, separate symptom from cause, refuse guess-based data
# recovery, migrate stored records safely, and bank a lesson only after the
# verdict closes. Same discipline as the two groups above — deliberately NOT in
# the universal corporate manifest, because an ordinary "fix this bug" turn must
# not pay for a reasoning protocol it is not going to use.
#
# docs/self-audit-lessons.md is deliberately NOT duplicated here: it is the
# historical record of defect classes already found and already belongs to the
# memory group. This group carries the protocol, not the history.
_SELF_REPAIR_DOCTRINE_DOC_PATHS = (
    "docs/SELF_REPAIR_DOCTRINE.md",
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
    # Operator *wording*, not module names. The `confidence` / `low_confidence_gate`
    # entries are the pre-2026-07-27 vocabulary, kept so a question phrased the old
    # way still routes here. The live module is `core/evidence_support.py` and the
    # live journal event is `evidence_support`.
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


def _wants_external_lookup(question: str) -> bool:
    """True when the question genuinely needs outside/web/current information or
    a comparison against a named external system — the one case where web egress
    on a self-referential question is still legitimate.

    Matching is token-boundary aware (see core.lang_match) so short function
    words cannot substring-collide with longer inflected words — e.g. the
    preposition "с" no longer matches inside "своё"."""
    return any_term_matches(question or "", _EXTERNAL_LOOKUP_TERMS)


def _is_self_repo_introspection_question(question: str) -> bool:
    """True when the question is purely about the agent's OWN private repo/self
    and carries no external-lookup intent. Such questions cannot be answered by
    the public web; a web_search only harvests irrelevant noise that pollutes
    the source registry."""
    if not (
        any_term_matches(question or "", _SELF_REPO_INTROSPECTION_TERMS)
        or _ru_pronoun_domain_introspection(tokenize(question or ""))
    ):
        return False
    return not _wants_external_lookup(question)


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


# Sub-agent governance is a THEMATIC sub-topic. A single strong term (subagent,
# delegation, team executor, …) is enough; weaker action terms (quarantine,
# retire, pause, trust boundary, lifecycle) only count when paired with an
# agent/role context word, so ordinary uses ("pause the build", "retire this
# feature flag") do not drag in the sub-agent doc. Deliberately two-tier to
# avoid the over-broadening that plain substring matching causes.
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


# Two-tier, same shape as the sub-agent detector above and for the same reason:
# plain substring matching on "memory" would fire on "not enough memory",
# "memorable", and every turn that merely mentions remembering something.
# Broadening one of these lists has regressed the planner before, so each
# carries paired negative tests.
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
    # both stems: "обучение/обученный" and "обучается/обучаться". Only ever
    # consulted together with a memory context word, so the wider stem cannot
    # fire on an unrelated training question.
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


# Two-tier, same shape and same reason as the two detectors above. The stakes
# here are the inverse of "too narrow": a bare "fix the bug in core/loop.py" is
# an ordinary task and must NOT drag in a reasoning protocol. So the strong list
# holds only vocabulary that is unambiguously about the *repair protocol itself*
# (root cause, fail-before, backfill, regression guard, idempotence), while
# ordinary repair words ("fix", "bug", "migration", "self-build") are context
# only and need an action word to fire. Each list carries paired negative tests.
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
    has_context = any(term in lowered for term in _SELF_REPAIR_DOC_CONTEXT_TERMS)
    has_action = any(term in lowered for term in _SELF_REPAIR_DOC_ACTION_TERMS)
    return has_context and has_action


def is_confidence_evidence_diagnostic_question(question: str) -> bool:
    lowered = (question or "").casefold()
    return any(term in lowered for term in CONFIDENCE_EVIDENCE_DIAGNOSTIC_TERMS)


def _requests_implementation_detail(question: str) -> bool:
    lowered = (question or "").casefold()
    return any(term in lowered for term in _IMPLEMENTATION_DETAIL_TERMS)


def _norm_source_path(path: str) -> str:
    return path.strip().casefold().replace("\\", "/")





def _file_read_source_spec(path: str) -> dict[str, Any]:
    return {
        "tool": "file_read",
        "arguments": {"path": path},
        "label": f"file:{path}",
        "expected_outcome": "Non-empty UTF-8 text from the requested local source.",
    }


def _is_low_signal_confidence_source(src: dict[str, Any]) -> bool:
    args = src.get("arguments") or {}
    path = args.get("path") if isinstance(args, dict) else None
    norm = _norm_source_path(path) if isinstance(path, str) else ""
    if src.get("tool") == "file_read" and norm == "readme.md":
        return True
    if src.get("tool") == "list_dir" and norm.rstrip("/") == "tools":
        return True
    return False


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
            ordered.append(_file_read_source_spec(path))
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
            ordered_docs.append(_file_read_source_spec(path))
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


def _ensure_thematic_docs_first(
    sources: list[dict[str, Any]],
    warnings: list[str],
    *,
    target_paths: tuple[str, ...],
    lead_paths: tuple[str, ...],
    warning_prefix: str,
) -> list[dict[str, Any]]:
    """Place one thematic doc group ahead of code, behind higher-priority groups.

    Single implementation for every conditionally routed doc group. Each group
    differs only in *which* docs it owns (`target_paths`), which groups outrank
    it (`lead_paths`), and how the injection is reported (`warning_prefix`);
    the ordering and de-duplication rule itself is an invariant and lives here
    once, so a new group cannot re-introduce a fixed bug by copying the shape.

    De-duplication is total: *every* request for a target doc is consumed, not
    just the first. Keeping later duplicates in the remainder made the agent
    `file_read` the same doctrine file twice and burn context for nothing.
    """
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
            ordered_target.append(_file_read_source_spec(path))
            injected.append(path)

    if injected:
        warnings.append(warning_prefix + ", ".join(injected))
    return rest[:insert_at] + ordered_target + rest[insert_at:]


def _ensure_subagent_governance_docs_first(
    sources: list[dict[str, Any]],
    warnings: list[str],
) -> list[dict[str, Any]]:
    """Ensure the thematic sub-agent lifecycle doc leads a sub-agent question.

    Placed right after any leading doctrine/corporate docs (so a broad
    doctrine+subagent question keeps corporate docs first, then the sub-agent
    contract), or at the very front when no corporate docs are present.
    """
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
    """Ensure the thematic memory docs lead a memory/durable-learning question.

    Placed after any leading doctrine/corporate docs AND after the sub-agent
    contract, so a question touching several themes keeps a stable order
    (corporate → sub-agent → memory) instead of the two thematic groups
    competing for the same slot.
    """
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
    """Ensure the self-repair protocol leads a self-diagnosis/repair question.

    Placed after any leading corporate, sub-agent AND memory docs, so a question
    touching several themes keeps a stable order (corporate → sub-agent →
    memory → self-repair) instead of the thematic groups competing for the same
    slot.
    """
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


# Web-egress tools that reach the public internet with an open-ended query.
# On a pure self-repo introspection question these can only return irrelevant
# hits that pollute the source registry, so they are dropped at plan time.
_WEB_EGRESS_TOOLS = frozenset({"web_search", "web_fetch", "rss_fetch"})


def _drop_web_lookup_for_introspection(
    sources: list[dict[str, Any]],
    warnings: list[str],
) -> list[dict[str, Any]]:
    """Remove web-egress steps from a plan for a self-repo introspection
    question. The public web cannot answer "what changed in your own repo / what
    bug is in your architecture / what's in your memory"; a web_search there only
    harvests noise (e.g. a CNN homepage) that then gets ingested as a source."""
    filtered: list[dict[str, Any]] = []
    for src in sources:
        if src.get("tool") in _WEB_EGRESS_TOOLS:
            label = src.get("label") or src.get("tool")
            warnings.append(
                f"{src.get('tool')} dropped for self-repo introspection question "
                f"(public web cannot answer it; would pollute the source registry): {label}"
            )
            continue
        filtered.append(src)
    return filtered

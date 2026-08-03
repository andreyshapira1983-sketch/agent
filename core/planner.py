"""LLM-driven Planner (§3 Cognitive Core: Planning).

The Planner sees the user's question and a list of available tools, then emits
a JSON plan describing which tools to invoke. It NEVER executes anything —
the Executor (AgentLoop) runs the plan.

Hard rules enforced here:
  - Tools not in the ToolRegistry are dropped.
  - file_read paths that don't match the user-provided hint are dropped
    (so the model cannot wander the workspace on its own).
  - web_search.max_results is clamped to [1, 10].
  - Malformed JSON falls back to an empty plan; the loop then answers from
    general knowledge with explicit "general-knowledge" sourcing.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.doc_routing import (
    _drop_readme_status_sources,
    _drop_web_lookup_for_introspection,
    _ensure_confidence_evidence_sources_first,
    _ensure_doctrine_docs_first,
    _ensure_memory_governance_docs_first,
    _ensure_self_repair_doctrine_docs_first,
    _ensure_subagent_governance_docs_first,
    _explicitly_requests_readme,
    _is_memory_governance_question,
    _is_self_repair_doctrine_question,
    _is_self_repo_introspection_question,
    _is_subagent_governance_question,
    _requests_implementation_detail,
    _should_prefer_memory_over_readme,
    is_confidence_evidence_diagnostic_question,
    is_doctrine_corporate_question,
)
from core.host_tools_context import _build_host_tools_block
from core.llm import LLM
from core.plan_parsing import parse_json
from core.planner_prompt import PLANNER_SYSTEM
from core.step_sanitizer import sanitize_step
from tools.base import ToolRegistry

# Output budget for a single planning call.
#
# The previous hard-coded 1024 was too tight for a multi-step JSON plan, and on
# OpenAI reasoning models it was actively harmful: `max_completion_tokens`
# covers internal reasoning first, so the entire budget could be spent thinking
# and the API would return success with an EMPTY answer. The run log showed the
# planner burning 1024 + 1024 tokens across an auto-continue round and parsing
# zero characters both times.
#
# `core.llm` raises this further for reasoning models (see
# `_REASONING_TOKEN_FLOOR`); the two floors compose, neither lowers the other.
_PLAN_MAX_TOKENS_DEFAULT = 2048


def _plan_max_tokens() -> int:
    """Per-call planner output budget, overridable via ``AGENT_PLAN_MAX_TOKENS``.

    Falls back to the default when the variable is missing, non-numeric, or
    non-positive, so a malformed environment can never produce a zero budget
    (which would guarantee an empty plan on every run).
    """
    raw = os.getenv("AGENT_PLAN_MAX_TOKENS")
    if raw is None:
        return _PLAN_MAX_TOKENS_DEFAULT
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        return _PLAN_MAX_TOKENS_DEFAULT
    return value if value > 0 else _PLAN_MAX_TOKENS_DEFAULT




@dataclass
class PlannerOutput:
    reasoning: str
    sources: list[dict[str, Any]]
    raw_response: str
    warnings: list[str] = field(default_factory=list)
    # Tool names that the planner requested but were not in the registry.
    # Non-empty means the LLM hallucinated a tool name; the plan was
    # silently down-scoped.  Surfaced via a ``plan_tool_drop`` log event
    # so operators can detect hallucination without digging through raw warnings.
    dropped_tools: list[str] = field(default_factory=list)
    # Structured, human-readable JSON-parse diagnostics (TD-003). Populated by
    # ``core.plan_parsing.parse_json`` so operators can see *why* an output failed to parse
    # (brief reason, which stage broke, whether a JSON block was found, which
    # fallback was chosen) plus a sanitised, length-limited preview of the raw
    # output — without another LLM call and without leaking full secrets.
    diagnostics: dict[str, Any] = field(default_factory=dict)


class LLMPlanner:
    """Asks the LLM to choose tools. Validates and sanitises the result."""

    # Self-documentation files the planner may read EVEN WITHOUT a
    # `--file hint`. The allowlist is intentionally tiny:
    #   * narrow scope (just project documentation),
    #   * ASCII-only paths (so the existing identifier policy doesn't
    #     fight us),
    #   * read-only operation,
    #   * answers exactly the "introspection" use case that motivated
    #     this exception (see MVP-14.4.x notes).
    # Any other file requires the user to pass `--file <path>`.
    DEFAULT_SELF_DOCUMENTATION_PATHS: tuple[str, ...] = ("README.md", "tools/")

    def __init__(
        self,
        llm: LLM,
        registry: ToolRegistry,
        self_documentation_paths: tuple[str, ...] | None = None,
    ):
        self.llm = llm
        self.registry = registry
        # Run-scoped set of tool names to hide from the planner surface. Empty
        # by default (REPL sees every registered tool). AutonomousRuntime._task_goal
        # sets this to the run-scoped block set so the planner never *proposes*
        # tools that PolicyGate would deny on the unattended goal path. Policy
        # remains the defense-in-depth block at execution time.
        self.hidden_tools: frozenset[str] = frozenset()
        # Defensive copy + validation: every entry must be a relative
        # ASCII path with no traversal. If the caller passes garbage,
        # we fall back to the default rather than crashing.
        if self_documentation_paths is None:
            self.self_documentation_paths = self.DEFAULT_SELF_DOCUMENTATION_PATHS
        else:
            self.self_documentation_paths = tuple(
                p.strip()
                for p in self_documentation_paths
                if isinstance(p, str)
                and p.strip()
                and p.isascii()
                and ".." not in p
                and not p.startswith(("/", "\\"))
                and ":" not in p
            )

    def plan(
        self,
        question: str,
        file_hint: str | None,
        history: str = "",
        failure_context: str = "",
        forbidden_actions: tuple[tuple[str, str], ...] = (),
        llm=None,
    ) -> PlannerOutput:
        """Ask the LLM for a plan.

        `failure_context` is the formatted `<replan_context>` block built by
        `AgentLoop` from previous attempts' `ReplanTrigger`s. Empty on the
        first attempt; non-empty on every replan. The block sits AFTER
        conversation history and IMMEDIATELY BEFORE the question so the
        model reads the failure right before it decides what to try.

        `forbidden_actions` (MVP-12) is a tuple of (tool, args_json) pairs
        the sanitiser must REJECT. Populated by `ReplanPolicy` for
        failures whose budget has `requires_different_action=True`
        (approval_deny, policy_blocked, etc.).

        `llm` — optional per-call override (adaptive routing). When provided,
        it replaces `self.llm` for this single call only.
        """
        user_prompt = self._build_user_prompt(
            question, file_hint, history, failure_context
        )
        # Kernel-side defense: redact credentials and sensitive PII before
        # either can reach the LLM provider. Clean prompts pass through.
        from core.redaction import redact_dlp_text  # local import: avoid cycles
        safe_prompt, _secret_findings, _pii_findings = redact_dlp_text(user_prompt)
        _active_llm = llm if llm is not None else self.llm
        # Inject dynamic host-tools block so the planner knows what is
        # actually installed on this machine (from .env BLENDER_PATH etc.)
        host_block = _build_host_tools_block()
        effective_system = PLANNER_SYSTEM + host_block if host_block else PLANNER_SYSTEM
        raw = _active_llm.complete(
            system=effective_system,
            user=safe_prompt,
            max_tokens=_plan_max_tokens(),
            temperature=0.0,
        )
        parsed, parse_warnings, parse_diag = parse_json(raw)
        if parsed is None:
            return PlannerOutput(
                reasoning="(planner output did not parse — falling back to empty plan)",
                sources=[],
                raw_response=raw,
                warnings=parse_warnings + ["plan_parse_failed"],
                diagnostics=parse_diag,
            )

        reasoning = str(parsed.get("reasoning", "")).strip() or "(no reasoning provided)"
        raw_steps = parsed.get("steps") or []
        if not isinstance(raw_steps, list):
            parse_diag = {**parse_diag, "reason": "steps field was not a list"}
            return PlannerOutput(
                reasoning=reasoning,
                sources=[],
                raw_response=raw,
                warnings=parse_warnings + ["steps_field_not_a_list"],
                diagnostics=parse_diag,
            )

        sources, step_warnings, dropped_tools = self._validate_steps(
            raw_steps, file_hint, forbidden_actions
        )
        if _should_prefer_memory_over_readme(question, history):
            sources = _drop_readme_status_sources(sources, step_warnings)
        if _is_self_repo_introspection_question(question):
            sources = _drop_web_lookup_for_introspection(sources, step_warnings)
        if is_confidence_evidence_diagnostic_question(question):
            if "file_read" in self.hidden_tools:
                step_warnings.append(
                    "confidence/evidence verifier sources required but file_read is hidden on this path"
                )
            else:
                try:
                    self.registry.get("file_read")
                except KeyError:
                    step_warnings.append(
                        "confidence/evidence verifier sources required but file_read is not registered"
                    )
                else:
                    sources = _ensure_confidence_evidence_sources_first(
                        sources,
                        step_warnings,
                        drop_low_signal_defaults=(
                            not _explicitly_requests_readme(question)
                        ),
                    )
        if is_doctrine_corporate_question(question):
            if "file_read" in self.hidden_tools:
                step_warnings.append(
                    "doctrine/corporate docs required but file_read is hidden on this path"
                )
            else:
                try:
                    self.registry.get("file_read")
                except KeyError:
                    step_warnings.append(
                        "doctrine/corporate docs required but file_read is not registered"
                    )
                else:
                    sources = _ensure_doctrine_docs_first(
                        sources,
                        step_warnings,
                        drop_default_code_sources=(
                            not _requests_implementation_detail(question)
                            and not _explicitly_requests_readme(question)
                        ),
                    )
        if _is_subagent_governance_question(question) and self._file_read_available():
            sources = _ensure_subagent_governance_docs_first(
                sources,
                step_warnings,
            )
        if _is_memory_governance_question(question) and self._file_read_available():
            sources = _ensure_memory_governance_docs_first(
                sources,
                step_warnings,
            )
        if _is_self_repair_doctrine_question(question) and self._file_read_available():
            sources = _ensure_self_repair_doctrine_docs_first(
                sources,
                step_warnings,
            )
        # Coverage enforcement: if the question is about test adequacy /
        # coverage and the planner produced a run_tests step without
        # coverage=True, inject it automatically so the synthesizer always
        # gets real coverage data instead of just pass counts.
        _COVERAGE_KEYWORDS = (
            "хватает ли тест", "покрывают ли", "достаточно тест",
            "enough test", "test coverage", "coverage report",
            "покрытие", "не протестирован", "are all modules tested",
        )
        q_lower = question.lower()
        if any(kw in q_lower for kw in _COVERAGE_KEYWORDS):
            for src in sources:
                if src.get("tool") == "run_tests":
                    src.setdefault("arguments", {})
                    if not src["arguments"].get("coverage"):
                        src["arguments"]["coverage"] = True
        return PlannerOutput(
            reasoning=reasoning,
            sources=sources,
            raw_response=raw,
            warnings=parse_warnings + step_warnings,
            dropped_tools=dropped_tools,
            diagnostics=parse_diag,
        )

    # ---------- prompt construction ----------

    def _file_read_available(self) -> bool:
        """Whether a `file_read` step can actually run on this path.

        Doc injection and the matching prompt directive must agree. Injection
        was already gated on this; the directives were not, so on the
        autonomous path (where `file_read` is hidden) the prompt still ordered
        the model to "read docs/X first" while the registered-tools list and
        the [UNAVAILABLE_TOOLS=...] block said it could not. That contradiction
        is exactly the noisy policy_blocked replan the hidden-tools directive
        exists to prevent, so the gate lives here once and both sides use it.
        """
        if "file_read" in (getattr(self, "hidden_tools", frozenset()) or frozenset()):
            return False
        try:
            self.registry.get("file_read")
        except KeyError:
            return False
        return True

    def _build_user_prompt(
        self,
        question: str,
        file_hint: str | None,
        history: str = "",
        failure_context: str = "",
    ) -> str:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        hint = file_hint or "(none)"
        hidden = getattr(self, "hidden_tools", frozenset()) or frozenset()
        tool_names = ", ".join(
            t.name for t in self.registry.list() if t.name not in hidden
        )
        # When tools are hidden for this path (autonomous goal path), do not
        # merely omit them — the static system prompt still describes tools like
        # spawn_subagent. Add an explicit directive so the planner never selects
        # a run-scoped-blocked tool (avoids noisy policy_blocked replans).
        if hidden:
            unavailable_block = (
                f"[UNAVAILABLE_TOOLS={', '.join(sorted(hidden))} — these tools are "
                "NOT available on this path and MUST NOT appear in your plan. "
                "Ignore any general guidance that suggests them; use only the "
                "registered tools listed above.]\n"
            )
        else:
            unavailable_block = ""

        from core.task_complexity import (
            needs_live_grounding,  # local import: avoid cycles
        )
        if needs_live_grounding(question):
            grounding_block = (
                "\n[LIVE_GROUNDING=required — this question asks about current, "
                "recent, or time-sensitive information. Your plan MUST start with "
                "web_search to retrieve fresh data BEFORE the synthesiser answers. "
                "Do NOT rely on training knowledge alone for facts that change over time.]\n"
            )
        else:
            grounding_block = ""

        history_block = (
            f"<conversation_history>\n{history}\n</conversation_history>\n\n"
            if history.strip()
            else ""
        )
        project_memory_block = ""
        if _should_prefer_memory_over_readme(question, history):
            project_memory_block = (
                "[PROJECT_STATUS_MEMORY=preferred — long_term_memory already "
                "contains recent project/status records. Do NOT plan "
                "file_read README.md for live project status. README.md may "
                "only be used when the user explicitly asks for README or "
                "architecture/reference facts.]\n"
            )
        doctrine_docs_block = ""
        # Every "read docs/X first" directive is gated on file_read actually
        # being usable — an unreachable instruction only produces plans the
        # policy layer then blocks.
        docs_readable = self._file_read_available()
        if docs_readable and is_doctrine_corporate_question(question):
            doctrine_docs_block = (
                "[DOCTRINE_DOCS=required — for corporate model, central agent "
                "governance, subagents, self-build, night observation, and "
                "safe autonomy questions, start with docs/future/CORPORATE_MODEL.md, "
                "docs/CENTRAL_AGENT_GOVERNANCE.md, docs/AGENT_ANATOMY.md, "
                "docs/ROADMAP.md, and docs/COMMANDS_MAP.md before central "
                "core/*.py mechanics.]\n"
            )
        subagent_docs_block = ""
        if docs_readable and _is_subagent_governance_question(question):
            subagent_docs_block = (
                "[SUBAGENT_DOCS=required — this question is about sub-agents / "
                "delegation / team executor / role trust / quarantine / pause / "
                "retire / lifecycle. Read docs/SUBAGENT_LIFECYCLE.md first (the "
                "normative sub-agent lifecycle contract) before core/*.py "
                "mechanics.]\n"
            )
        memory_docs_block = ""
        if docs_readable and _is_memory_governance_question(question):
            memory_docs_block = (
                "[MEMORY_DOCS=required — this question is about memory / "
                "episodic / procedural / consolidation / forgetting / retrieval "
                "/ durable learning. Read docs/audit/MEMORY_MAP.md (how memory "
                "actually flows today), docs/MEMORY_SYSTEM_AUDIT.md and "
                "docs/self-audit-lessons.md first, before core/*.py mechanics. "
                "These record known defects and their causes — do not "
                "re-derive them from the code.]\n"
            )
        self_repair_docs_block = ""
        if docs_readable and _is_self_repair_doctrine_question(question):
            self_repair_docs_block = (
                "[SELF_REPAIR_DOCS=required — this question is about "
                "self-diagnosis / self-repair / root cause / regression / "
                "backfill / data migration. Read docs/SELF_REPAIR_DOCTRINE.md "
                "first (the normative repair protocol: prove the defect, "
                "separate symptom from cause, never reconstruct data by guess, "
                "fail closed, migrate safely, bank the lesson only after the "
                "verdict closes) before core/*.py mechanics.]\n"
            )
        confidence_evidence_block = ""
        if is_confidence_evidence_diagnostic_question(question):
            confidence_evidence_block = (
                "[CONFIDENCE_EVIDENCE_DIAGNOSTIC_SOURCES=required — for questions "
                "about evidence support, evidence, citations, "
                "verifier, verified/unverified chunks, or source registry, start "
                "with core/verifier.py, tests/test_verifier.py, "
                "tests/test_evidence_support.py, and "
                "tests/test_confidence_vector.py. Do NOT use README.md or "
                "list_dir tools/ as primary evidence for confidence-gate internals.]\n"
            )
        # Replan context sits between history and question — close enough
        # to the question to be salient, but separated from old turns so
        # the model doesn't confuse "what I tried this cycle" with "what I
        # discussed in a prior turn".
        replan_block = (
            f"{failure_context.rstrip()}\n\n" if failure_context.strip() else ""
        )

        return (
            f"current_date: {today}\n"
            f"file hint: {hint}\n"
            f"registered tools: {tool_names}\n"
            f"{unavailable_block}"
            f"\n"
            f"{history_block}"
            f"{replan_block}"
            f"{grounding_block}"
            f"{project_memory_block}"
            f"{confidence_evidence_block}"
            f"{doctrine_docs_block}"
            f"{subagent_docs_block}"
            f"{memory_docs_block}"
            f"{self_repair_docs_block}"
            f"question: {question}\n"
            f"\n"
            f"Return your JSON plan now."
        )

    # ---------- step validation ----------

    def _validate_steps(
        self,
        raw_steps: list[Any],
        file_hint: str | None,
        forbidden_actions: tuple[tuple[str, str], ...] = (),
    ) -> tuple[list[dict[str, Any]], list[str], list[str]]:
        sources: list[dict[str, Any]] = []
        warnings: list[str] = []
        dropped_tools: list[str] = []

        forbidden_set: set[tuple[str, str]] = set(forbidden_actions)

        for idx, step in enumerate(raw_steps):
            if not isinstance(step, dict):
                warnings.append(f"step[{idx}]: not an object, dropped")
                continue

            tool_name = step.get("tool")
            args = step.get("arguments") or {}
            if not isinstance(tool_name, str) or not isinstance(args, dict):
                warnings.append(f"step[{idx}]: missing tool name or arguments, dropped")
                continue

            # Unknown tool -> drop (do not let the planner widen the surface).
            try:
                self.registry.get(tool_name)
            except KeyError:
                warnings.append(f"step[{idx}]: tool '{tool_name}' not registered, dropped")
                # Track hallucinated tool names separately so the caller can
                # emit a structured log event (plan_tool_drop) without
                # parsing free-text warning strings.
                dropped_tools.append(tool_name)
                continue

            # MVP-12 forbidden-action gate. If the ReplanPolicy marked an
            # earlier (tool, args) pair as no-retry (approval_deny,
            # policy_blocked, etc.), the planner is not allowed to revive
            # it even if the LLM tries again. Canonicalise args the same
            # way ReplanPolicy did (sorted JSON keys).
            try:
                canonical_args = json.dumps(args, sort_keys=True, ensure_ascii=False)
            except TypeError:
                canonical_args = ""
            if canonical_args and (tool_name, canonical_args) in forbidden_set:
                warnings.append(
                    f"step[{idx}]: ({tool_name}, {canonical_args}) is in the "
                    f"forbidden_actions list from a prior failure, dropped"
                )
                continue

            spec = sanitize_step(
                tool_name, args, file_hint, idx, warnings,
                self_documentation_paths=self.self_documentation_paths,
            )
            if spec is None:
                continue
            sources.append(spec)

        return sources, warnings, dropped_tools

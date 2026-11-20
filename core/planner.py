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
    _ensure_confidence_evidence_sources_first,
    _ensure_doctrine_docs_first,
    _ensure_memory_governance_docs_first,
    _ensure_self_repair_doctrine_docs_first,
    _ensure_subagent_governance_docs_first,
    _explicitly_requests_readme,
    _is_memory_governance_question,
    _is_self_repair_doctrine_question,
    _is_subagent_governance_question,
    _requests_implementation_detail,
    _should_prefer_memory_over_readme,
    confidence_sources_present,
    doctrine_docs_present,
    is_confidence_evidence_diagnostic_question,
    is_doctrine_corporate_question,
)
from core.host_tools_context import _build_host_tools_block
from core.llm import LLM, accepted_flags
from core.plan_parsing import parse_json
from core.planner_prompt import PLANNER_SYSTEM, without_tool_blocks
from core.run_context import run_blocked_tools
from core.step_sanitizer import sanitize_step
from tools.base import ToolRegistry

# Output budget for one planning call: OpenAI reasoning models spend `max_completion_tokens` on reasoning first and can return an EMPTY answer; `core.llm` raises the floor further (`_REASONING_TOKEN_FLOOR`).
_PLAN_MAX_TOKENS_DEFAULT = 2048


def _plan_max_tokens() -> int:
    """Per-call planner output budget, overridable via ``AGENT_PLAN_MAX_TOKENS``; a missing, non-numeric or non-positive value falls back to the default."""
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
    # Tool names the planner requested that were not in the registry (hallucinated); surfaced via a ``plan_tool_drop`` log event.
    dropped_tools: list[str] = field(default_factory=list)
    # Structured JSON-parse diagnostics from ``core.plan_parsing.parse_json``: why parsing failed, plus a sanitised, length-limited preview of the raw output.
    diagnostics: dict[str, Any] = field(default_factory=dict)


class LLMPlanner:
    """Asks the LLM to choose tools. Validates and sanitises the result."""

    # Self-documentation files the planner may read even without `--file hint`; tiny allowlist: project documentation only, ASCII paths, read-only.
    # The successor of README.md is generated from core/ and guarded by scripts/agent_anatomy_check.py.
    DEFAULT_SELF_DOCUMENTATION_PATHS: tuple[str, ...] = (
        "knowledge/generated/AGENT_ANATOMY.md", "tools/",
    )

    def __init__(
        self,
        llm: LLM,
        registry: ToolRegistry,
        self_documentation_paths: tuple[str, ...] | None = None,
        workspace: str | None = None,
    ):
        # workspace None means "no address": no lesson reads and no receipts, so tests building a planner never write to live journals
        self.workspace = workspace
        self.llm = llm
        self.registry = registry
        # host-level hidden tool names (empty: the REPL sees all); the run's own set is read via `effective_hidden_tools`; policy blocks again at execution
        self.hidden_tools: frozenset[str] = frozenset()
        # defensive copy + validation: relative ASCII paths without traversal, else the default
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

    def lesson_block_for_prompt(self, question: str = "", file_hint: str | None = None) -> str:
        """Return the distilled-lessons block for the planner prompt; best-effort ("" on any failure).

        The injection receipt is written on delivery. With a question the block is scoped to lessons that apply; an unscoped call keeps every lesson.
        """
        try:
            if not self.workspace:
                return ""

            from core.causal_claim_store import (
                distilled_lessons,
                lesson_applies,
                lesson_block_for_prompt,
            )

            hint = file_hint or ""
            block = lesson_block_for_prompt(
                self.workspace, question=question, file_hint=hint,
            )
            if not block:
                return ""

            from core.lesson_provenance import record_lesson_injections
            record_lesson_injections(
                self.workspace,
                tuple(card for card in distilled_lessons(self.workspace)
                      if lesson_applies(card, question=question, file_hint=hint)),
                consumer="planner.plan",
            )
        except Exception:  # noqa: BLE001 — delivery must not break planning
            return ""
        else:
            return "\n" + block

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

        `forbidden_actions` is a tuple of (tool, args_json) pairs the sanitiser must REJECT (set by `ReplanPolicy` for failures that require a different action).
        `llm` optionally overrides `self.llm` for this call only (adaptive routing).
        """
        user_prompt = self._build_user_prompt(
            question, file_hint, history, failure_context
        )
        # redact credentials and sensitive PII before they reach the LLM provider
        from core.redaction import redact_dlp_text  # local import: avoid cycles
        safe_prompt, _secret_findings, _pii_findings = redact_dlp_text(user_prompt)
        _active_llm = llm if llm is not None else self.llm
        # dynamic host-tools block: what is actually installed on this machine
        host_block = _build_host_tools_block()
        # tools hidden on this path are not described: see without_tool_blocks
        base_system = without_tool_blocks(
            PLANNER_SYSTEM, self.effective_hidden_tools(),
        )
        effective_system = base_system + host_block if host_block else base_system
        effective_system += self.lesson_block_for_prompt(
            question, file_hint,
        )
        raw = _active_llm.complete(
            system=effective_system,
            user=safe_prompt,
            max_tokens=_plan_max_tokens(), temperature=0.0,
            **accepted_flags(_active_llm.complete, {"json_object": True}),
        )
        parsed, parse_warnings, parse_diag = parse_json(raw)
        if parsed is None:
            return PlannerOutput(
                reasoning="(planner output did not parse — falling back to empty plan)",
                sources=[],
                raw_response=raw,
                warnings=[*parse_warnings, "plan_parse_failed"],
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
                warnings=[*parse_warnings, "steps_field_not_a_list"],
                diagnostics=parse_diag,
            )

        sources, step_warnings, dropped_tools = self._validate_steps(
            raw_steps, file_hint, forbidden_actions
        )
        if _should_prefer_memory_over_readme(question, history):
            sources = _drop_readme_status_sources(sources, step_warnings)
        if is_confidence_evidence_diagnostic_question(question) and self._confidence_sources_here():
            if "file_read" in self.effective_hidden_tools():
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
        if is_doctrine_corporate_question(question) and self._confidence_sources_here(doctrine_docs_present):
            if "file_read" in self.effective_hidden_tools():
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
        if _is_subagent_governance_question(question) and self._file_read_available() and self._confidence_sources_here(doctrine_docs_present):
            sources = _ensure_subagent_governance_docs_first(
                sources,
                step_warnings,
            )
        if _is_memory_governance_question(question) and self._file_read_available() and self._confidence_sources_here(doctrine_docs_present):
            sources = _ensure_memory_governance_docs_first(
                sources,
                step_warnings,
            )
        if _is_self_repair_doctrine_question(question) and self._file_read_available() and self._confidence_sources_here(doctrine_docs_present):
            sources = _ensure_self_repair_doctrine_docs_first(
                sources,
                step_warnings,
            )
        # coverage enforcement: a test-adequacy question with a run_tests step lacking coverage=True gets it injected
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


    def _confidence_sources_here(self, present=confidence_sources_present) -> bool:
        """The route applies only where its files exist (`doc_routing`)."""
        try:
            return present(getattr(self.registry.get("file_read"), "workspace_root", None))
        except KeyError:
            return True

    def effective_hidden_tools(self) -> frozenset[str]:
        """The host's hidden set plus the current run's, each run its own."""
        return frozenset(getattr(self, "hidden_tools", frozenset()) or ()) | run_blocked_tools()

    def _file_read_available(self) -> bool:
        """Whether a `file_read` step can actually run on this path.

        Doc injection and the prompt directive must agree: where `file_read` is hidden (autonomous path), the prompt must not order reading docs.
        """
        if "file_read" in self.effective_hidden_tools():
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
        hidden = self.effective_hidden_tools()
        tool_names = ", ".join(
            t.name for t in self.registry.list() if t.name not in hidden
        )
        # argument contracts: without them the planner guessed
        _contracts = getattr(self.registry, "argument_contracts", lambda **_k: "")(hidden=frozenset(hidden))
        contracts_block = (
            f"tool argument contracts (use these names and values exactly):\n{_contracts}\n"
            if _contracts else ""
        )
        # hidden tools are not merely omitted: an explicit directive stops the planner selecting a blocked tool (avoids noisy policy_blocked replans)
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
                "file_read knowledge/generated/AGENT_ANATOMY.md for live "
                "project status. The anatomy map may only be used when the "
                "user explicitly asks for architecture/reference facts.]\n"
            )
        doctrine_docs_block = ""
        # every "read docs/X first" directive is gated on file_read being usable
        docs_readable = self._file_read_available() and self._confidence_sources_here(doctrine_docs_present)
        if docs_readable and is_doctrine_corporate_question(question):
            doctrine_docs_block = (
                "[DOCTRINE_DOCS=required — for corporate model, central agent "
                "governance, subagents, self-build, night observation, and "
                "safe autonomy questions, start with knowledge/doctrine/future/CORPORATE_MODEL.md, "
                "knowledge/doctrine/CENTRAL_AGENT_GOVERNANCE.md, knowledge/generated/AGENT_ANATOMY.md, "
                "knowledge/doctrine/ROADMAP.md, and knowledge/maps/COMMANDS_MAP.md before central "
                "core/*.py mechanics.]\n"
            )
        subagent_docs_block = ""
        if docs_readable and _is_subagent_governance_question(question):
            subagent_docs_block = (
                "[SUBAGENT_DOCS=required — this question is about sub-agents / "
                "delegation / team executor / role trust / quarantine / pause / "
                "retire / lifecycle. Read knowledge/doctrine/SUBAGENT_LIFECYCLE.md first (the "
                "normative sub-agent lifecycle contract) before core/*.py "
                "mechanics.]\n"
            )
        memory_docs_block = ""
        if docs_readable and _is_memory_governance_question(question):
            memory_docs_block = (
                "[MEMORY_DOCS=required — this question is about memory / "
                "episodic / procedural / consolidation / forgetting / retrieval "
                "/ durable learning. Read knowledge/doctrine/MEMORY_SYSTEM_AUDIT.md and "
                "knowledge/doctrine/self-audit-lessons.md first, before core/*.py mechanics. "
                "These record known defects and their CAUSES; do not re-derive "
                "those from the code. Their status ledgers are superseded — "
                "whether a defect is still open belongs to "
                "docs/audit/MASTER_ISSUE_REGISTRY.md, and where a document and "
                "the code disagree, the code wins.]\n"
            )
        self_repair_docs_block = ""
        if docs_readable and _is_self_repair_doctrine_question(question):
            self_repair_docs_block = (
                "[SELF_REPAIR_DOCS=required — this question is about "
                "self-diagnosis / self-repair / root cause / regression / "
                "backfill / data migration. Read knowledge/doctrine/SELF_REPAIR_DOCTRINE.md "
                "first (the normative repair protocol: prove the defect, "
                "separate symptom from cause, never reconstruct data by guess, "
                "fail closed, migrate safely, bank the lesson only after the "
                "verdict closes) before core/*.py mechanics.]\n"
            )
        confidence_evidence_block = ""
        if is_confidence_evidence_diagnostic_question(question) and self._confidence_sources_here():
            confidence_evidence_block = (
                "[CONFIDENCE_EVIDENCE_DIAGNOSTIC_SOURCES=required — for questions "
                "about evidence support, evidence, citations, "
                "verifier, verified/unverified chunks, or source registry, start "
                "with core/verifier.py, tests/test_verifier.py, "
                "tests/test_evidence_support.py, and "
                "tests/test_confidence_vector.py. Do NOT use "
                "knowledge/generated/AGENT_ANATOMY.md or list_dir tools/ as "
                "primary evidence for confidence-gate internals.]\n"
            )
        # replan context sits between history and question: salient, but separated from old turns
        replan_block = (
            f"{failure_context.rstrip()}\n\n" if failure_context.strip() else ""
        )

        return (
            f"current_date: {today}\n"
            f"file hint: {hint}\n"
            f"registered tools: {tool_names}\n"
            f"{contracts_block}"
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

            # unknown tool: drop (the planner must not widen the surface)
            try:
                self.registry.get(tool_name)
            except KeyError:
                warnings.append(f"step[{idx}]: tool '{tool_name}' not registered, dropped")
                # tracked separately so the caller can emit a structured plan_tool_drop event
                dropped_tools.append(tool_name)
                continue

            # forbidden-action gate: a (tool, args) pair marked no-retry by ReplanPolicy cannot be
            # revived; canonicalise args exactly as ReplanPolicy._forbidden_actions does
            # (sort_keys=True, ensure_ascii=False), or the membership test silently stops matching
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
                workspace=self.workspace,
            )
            if spec is None:
                continue
            # the step's rationale is kept: the sanitiser rebuilds the step from tool and arguments and would drop it
            rationale = step.get("rationale") if isinstance(step, dict) else None
            if isinstance(rationale, str) and rationale.strip():
                spec["rationale"] = rationale.strip()[:300]
            sources.append(spec)

        return sources, warnings, dropped_tools

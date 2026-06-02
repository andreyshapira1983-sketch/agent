"""SubAgent Runner — executes one bounded sub-agent contract using AgentLoop.

Design principles
-----------------
1. ISOLATION   — child gets its own TraceLogger (separate trace_id) and a
                 filtered ToolRegistry containing only read-only tools
                 explicitly listed in ``allowed_tools``.  ``spawn_subagent``
                 is NEVER included, so recursion is prevented structurally.

2. BOUNDED     — ``max_replan_attempts=1`` (no replanning inside a sub-agent).

3. TRANSPARENT — every spawning is logged by the parent loop as a normal
                 ``tool_call`` / ``tool_result`` event pair, so the audit
                 trail is complete.

4. NO CYCLES   — this module uses ``from core.loop import AgentLoop`` only
                 inside ``SubAgentRunner.run()`` (lazy import) to avoid the
                 circular dependency: loop.py → SpawnSubagentTool
                 → subagent_runner.py → loop.py.

Typical flow
------------
  parent AgentLoop
    → planner emits spawn_subagent step
    → _execute_step calls SpawnSubagentTool.run()
    → SpawnSubagentTool calls SubAgentRunner.run()
    → SubAgentRunner creates isolated child AgentLoop
    → child runs its objective, returns an answer string
    → SubAgentRunResult.to_evidence_text() flows back as Evidence
      to parent's synthesizer, cited as [subagent:<name>]
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tools.base import ToolRegistry

if TYPE_CHECKING:
    from core.llm import LLM
    from core.logger import TraceLogger
    from core.policy import PolicyGate
    from core.model_router import ModelRouter

# Tools that sub-agents may use — all read-only or low-risk.
# ``spawn_subagent`` and ``shell_exec`` are intentionally absent.
_SAFE_SUBAGENT_TOOLS: frozenset[str] = frozenset({
    "file_read",
    "list_dir",
    "web_search",
    "web_fetch",
    "rss_fetch",
    "semantic_scholar_search",
    "run_tests",
    "read_logs",
    "diff_file",
})

# Hard ceiling on how many tool calls a single sub-agent may make.
MAX_SUBAGENT_TOOL_CALLS = 3

# ---------------------------------------------------------------------------
# Structural quality signals (no LLM call)
# ---------------------------------------------------------------------------

# Phrases that suggest the sub-agent hedged instead of answering
_HEDGING_PHRASES: frozenset[str] = frozenset({
    "i cannot", "i can't", "unable to", "i don't know",
    "i do not know", "not sure", "i'm not sure",
    "cannot be determined", "no information available",
    "i am unable",
    # Russian hedging phrases (sub-agent answers may be in Russian)
    "не могу", "не знаю", "затрудняюсь",
    "невозможно определить", "нет информации",
    "не уверен", "не уверена", "не удалось", "невозможно",
})

# Keywords that indicate research / multi-step work worth spawning
_RESEARCH_KEYWORDS: frozenset[str] = frozenset({
    "research", "find", "search", "look up", "investigate",
    "analyze", "analyse", "compare", "summarize", "summarise",
    "review", "audit", "benchmark", "evaluate", "read",
    "fetch", "collect", "gather", "enumerate", "list all",
})

# System prompt for the LLM quality judge (Level 2 — AIS-inspired)
_JUDGE_SYSTEM = (
    "You are a quality evaluator for AI sub-agent answers. "
    "Rate the answer on a scale of 1–5:\n"
    "1 = Refuses / irrelevant / empty\n"
    "2 = Vague or heavily hedged, minimal specifics\n"
    "3 = Partially addresses the objective\n"
    "4 = Mostly complete, specific, on-topic\n"
    "5 = Thorough, specific, directly addresses the objective\n"
    "Respond with ONLY the integer (1–5). No explanation."
)


def _compute_structural_confidence(answer: str) -> float:
    """Heuristic confidence score for a sub-agent answer, in [0.0, 1.0].

    No LLM call — purely structural signals inspired by 'More Agents Is All
    You Need' (Li et al., 2024) observation that answer quality correlates
    with specificity and lack of hedging.

    Signals used:
    * Length   — very short answers are likely low-quality
    * Hedging  — phrases like "I cannot" / "I don't know" reduce score
    * Specificity — numbers, URLs, code blocks suggest concrete content
    """
    if not answer or not answer.strip():
        return 0.0

    text = answer.lower()
    n = len(answer.strip())

    # Length baseline
    if n < 50:
        length_score = 0.10
    elif n < 150:
        length_score = 0.40
    elif n < 500:
        length_score = 0.65
    else:
        length_score = 0.80

    # Hedging penalty
    hedging_penalty = 0.30 if any(p in text for p in _HEDGING_PHRASES) else 0.0

    # Specificity bonus
    has_numbers = bool(re.search(r"\b\d+(?:\.\d+)?\b", answer))
    has_url = "http" in text or "www." in text
    has_code = "```" in answer or ("`" in answer and answer.count("`") >= 2)
    specificity_count = sum([has_numbers, has_url, has_code])
    specificity_bonus = 0.10 * min(specificity_count, 2)  # max +0.20

    score = length_score - hedging_penalty + specificity_bonus
    return max(0.0, min(1.0, round(score, 2)))


def _estimate_complexity(objective: str) -> str:
    """Classify objective complexity as 'trivial', 'standard', or 'complex'.

    Based on 'More Agents Is All You Need' (Li et al., 2024): performance
    gains from multi-agent work are near-zero for trivially easy tasks
    (low inherent difficulty, few reasoning steps) and peak at medium
    difficulty.

    'trivial'  → short, no research keywords; parent synthesizer could answer
    'standard' → typical independent sub-task worth spawning
    'complex'  → multi-step research; benefits most from a dedicated agent
    """
    word_count = len(objective.split())
    text = objective.lower()
    has_research_kw = any(kw in text for kw in _RESEARCH_KEYWORDS)

    if word_count < 10 and not has_research_kw:
        return "trivial"
    if word_count >= 30 or (has_research_kw and word_count >= 15):
        return "complex"
    return "standard"


@dataclass(frozen=True)
class SubAgentRunResult:
    """Structured result returned by SubAgentRunner.run().

    The parent loop embeds ``to_evidence_text()`` into its evidence chain so
    the synthesiser can cite sub-agent findings with a stable label.

    Quality fields
    --------------
    confidence_score : float [0.0, 1.0]
        Structural heuristic: length + specificity - hedging.  No extra
        LLM call — computed from the answer text alone.
    quality_score : int [1–5], 0 = not judged
        LLM judge score (Agent Importance Score, DyLAN-inspired).
        0 means the judge call failed or was skipped.
    complexity_tier : str  "trivial" | "standard" | "complex"
        Pre-run estimate of the objective complexity.  Surfaces in the
        Evidence so the parent synthesiser can learn when sub-agents are
        over-allocated.
    """

    contract_name: str   # human-readable identifier, e.g. "WebResearcher"
    role: str            # role description, e.g. "Web research specialist"
    objective: str       # what the sub-agent was asked to do
    answer: str          # the sub-agent's Output-Contract response
    trace_id: str        # child loop's trace_id for cross-referencing logs
    status: str          # "success" | "error"
    error: str = ""      # non-empty only when status == "error"
    confidence_score: float = 0.0   # structural quality signal [0.0, 1.0]
    quality_score: int = 0          # LLM judge score [1-5], 0 = not judged
    complexity_tier: str = "standard"  # "trivial" | "standard" | "complex"

    def to_evidence_text(self) -> str:
        """Format for injection into the parent loop's Evidence chain."""
        lines = [
            f"SubAgent name={self.contract_name!r}",
            f"  role           : {self.role}",
            f"  objective      : {self.objective}",
            f"  status         : {self.status}",
            f"  trace_id       : {self.trace_id}",
            f"  complexity     : {self.complexity_tier}",
            f"  confidence     : {self.confidence_score:.2f}",
        ]
        if self.quality_score > 0:
            lines.append(f"  quality_score  : {self.quality_score}/5")
        if self.complexity_tier == "trivial":
            lines.append(
                "  NOTE: objective was classified as 'trivial' — "
                "consider answering inline in future plans."
            )
        if self.status == "success":
            lines.append(f"  answer    :\n{self.answer}")
        else:
            lines.append(f"  error     : {self.error}")
        return "\n".join(lines)

    @property
    def label(self) -> str:
        """Citation label for the Output Contract (e.g. [subagent:WebResearcher])."""
        return f"subagent:{self.contract_name}"


class SubAgentRunner:
    """Creates and runs a bounded child AgentLoop for one sub-agent contract.

    Parameters
    ----------
    workspace_root:
        Absolute path to the workspace.  Forwarded to tools that need it
        (``FileReadTool``, ``ListDirTool``, etc.).
    policy:
        The same PolicyGate as the parent.  Sub-agents share the policy
        contract — they cannot bypass rules the parent is subject to.
    model_router:
        The parent's ModelRouter.  Sub-agents reuse the same LLM pool and
        usage ledger, so their token spend is accounted for correctly.
    parent_registry:
        The parent's full ToolRegistry.  Used to clone the subset of tools
        the sub-agent is allowed to use.
    log_dir:
        Directory where child trace logs are written.
    """

    def __init__(
        self,
        workspace_root: Path | str,
        policy: "PolicyGate",
        model_router: "ModelRouter",
        parent_registry: ToolRegistry,
        log_dir: Path | str,
    ) -> None:
        self.workspace_root = Path(workspace_root)
        self.policy = policy
        self.model_router = model_router
        self.parent_registry = parent_registry
        self.log_dir = Path(log_dir)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        contract_name: str,
        role: str,
        objective: str,
        context: str = "",
        allowed_tools: list[str] | None = None,
    ) -> SubAgentRunResult:
        """Execute one sub-agent contract and return its result.

        The child AgentLoop is created fresh for each call and discarded
        afterwards.  There is no shared mutable state between runs.
        """
        # Lazy import — breaks the circular dependency with core/loop.py.
        from core.loop import AgentLoop, new_trace_id  # noqa: PLC0415
        from core.logger import TraceLogger             # noqa: PLC0415

        # Level 3: classify complexity BEFORE spinning up a child loop so
        # the parent can see the tier even on error paths.
        complexity_tier = _estimate_complexity(objective)

        child_registry = self._build_child_registry(allowed_tools)
        question = self._build_question(role, objective, context)
        child_trace_id = new_trace_id()
        child_logger = TraceLogger(
            trace_id=child_trace_id,
            log_dir=self.log_dir,
            verbose=False,  # sub-agents are quiet; parent controls verbosity
        )
        child_logger.log(
            "subagent_start",
            {
                "contract_name": contract_name,
                "role": role,
                "objective": objective,
                "complexity_tier": complexity_tier,
                "allowed_tools": [t.name for t in child_registry.list()],
            },
        )

        try:
            child_loop = AgentLoop(
                registry=child_registry,
                policy=self.policy,
                llm=self.model_router.for_role("synthesizer"),
                logger=child_logger,
                model_router=self.model_router,
                # Sub-agents don't carry persistent or episodic memory —
                # they operate on the context passed in via `question`.
                memory=None,
                persistent_store=None,
                # One attempt only: if the plan fails, the sub-agent
                # returns what it has rather than burning more budget.
                max_replan_attempts=1,
                verifier_enabled=False,  # sub-agent answers are reviewed by parent
            )
            answer = child_loop.run(user_question=question)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            child_logger.log(
                "subagent_done",
                {"contract_name": contract_name, "status": "error", "error": err},
            )
            return SubAgentRunResult(
                contract_name=contract_name,
                role=role,
                objective=objective,
                answer="",
                trace_id=child_trace_id,
                status="error",
                error=err,
                complexity_tier=complexity_tier,
            )

        # ── child succeeded — Fix #5: re-scan answer for prompt injection BEFORE
        # embedding it in the parent loop's Evidence chain.
        # A hostile web page or file that triggered a 'suspicious' verdict (not
        # 'blocked') at the tool level may still have slipped through with an
        # annotation.  When the sub-agent's answer propagates to the parent as
        # evidence text the malicious payload would reach the parent LLM's
        # context unchanged.  We do a second scan here to catch that.
        from core.injection_guard import annotate_suspicious, scan_for_injection  # noqa: PLC0415
        _injection_scan = scan_for_injection(answer)
        if _injection_scan.verdict == "blocked":
            child_logger.log(
                "subagent_injection_blocked",
                {
                    "contract": contract_name,
                    "findings": len(_injection_scan.findings),
                },
            )
            return SubAgentRunResult(
                contract_name=contract_name,
                role=role,
                objective=objective,
                answer="",
                trace_id=child_trace_id,
                status="error",
                error=(
                    f"injection_blocked: sub-agent answer contained injection "
                    f"patterns ({len(_injection_scan.findings)} findings)"
                ),
                complexity_tier=complexity_tier,
            )
        elif _injection_scan.verdict == "suspicious":
            child_logger.log(
                "subagent_injection_suspicious",
                {
                    "contract": contract_name,
                    "findings": len(_injection_scan.findings),
                },
            )
            answer = annotate_suspicious(answer, source_id=f"subagent:{contract_name}")

        # ── compute quality signals (Levels 1 & 2) ──
        # Level 1: structural heuristic (no extra API call)
        confidence_score = _compute_structural_confidence(answer)
        # Level 2: LLM judge — AIS-inspired (one lightweight API call;
        # gracefully returns 0 on any failure so it never blocks the run)
        quality_score = self._judge_answer(objective, answer)

        child_logger.log(
            "subagent_done",
            {
                "contract_name": contract_name,
                "status": "success",
                "confidence_score": confidence_score,
                "quality_score": quality_score,
                "complexity_tier": complexity_tier,
            },
        )
        return SubAgentRunResult(
            contract_name=contract_name,
            role=role,
            objective=objective,
            answer=answer,
            trace_id=child_trace_id,
            status="success",
            confidence_score=confidence_score,
            quality_score=quality_score,
            complexity_tier=complexity_tier,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _judge_answer(self, objective: str, answer: str) -> int:
        """Rate sub-agent answer quality via a single lightweight LLM call.

        Inspired by DyLAN's Agent Importance Score: ask a downstream
        evaluator to rate the upstream agent's output 1–5, then propagate
        that score back to the parent as a quality signal.

        Returns int 1–5 on success, 0 if the judge call fails (network
        error, parse error, or mock LLM in tests).  0 means 'not judged'
        and must not be treated as a low score.
        """
        try:
            judge_llm = self.model_router.for_role("synthesizer")
            user_prompt = (
                f"Objective: {objective[:300]}\n\n"
                f"Sub-agent answer:\n{answer[:800]}\n\n"
                "Rating (1-5):"
            )
            response = judge_llm.complete(
                system=_JUDGE_SYSTEM,
                user=user_prompt,
                max_tokens=8,
                temperature=0.0,
            )
            # Guard: real LLM always returns str; mock objects do not
            if not isinstance(response, str):
                return 0
            m = re.search(r"[1-5]", response)
            return int(m.group()) if m else 0
        except Exception:
            return 0

    def _build_child_registry(self, allowed_tools: list[str] | None) -> ToolRegistry:
        """Return a filtered ToolRegistry for the child loop.

        Only tools in ``_SAFE_SUBAGENT_TOOLS`` ∩ ``allowed_tools`` are
        included.  ``spawn_subagent`` and ``shell_exec`` are always excluded,
        regardless of what the caller requests.
        """
        if allowed_tools is None:
            requested = _SAFE_SUBAGENT_TOOLS
        else:
            # Intersect with the safe set — the LLM cannot expand scope by
            # listing a dangerous tool in ``allowed_tools``.
            requested = _SAFE_SUBAGENT_TOOLS & set(allowed_tools)

        child_reg = ToolRegistry()
        for tool in self.parent_registry.list():
            if tool.name in requested:
                child_reg.register(tool)
        return child_reg

    def _build_question(self, role: str, objective: str, context: str) -> str:
        """Compose the child loop's user question from role + objective + context."""
        parts = [
            f"[Роль: {role}]",
            f"Задача: {objective}",
        ]
        if context.strip():
            parts.append(f"Контекст от родительского агента:\n{context.strip()}")
        return "\n\n".join(parts)

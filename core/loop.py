"""Control Loop — Observe -> Interpret -> Plan -> Act -> Verify -> Respond.

This is the §3 cycle from the architecture, minimal but real:
  - Every phase produces a typed data model.
  - Every phase emits a structured log line.
  - Every action passes through the Policy Gate before execution.
  - A Plan may now contain multiple steps (file_read + web_search + ...).
  - Each artifact is labelled with its source so the Output Contract
    can cite it back to the user.

MVP-8 — Re-planning. The plan→execute→verify pipeline is wrapped in a
bounded retry loop. When every step in a plan fails (and the plan is
non-empty), the agent asks the planner for a NEW plan and shows it
exactly what went wrong via a `<replan_context>` block. Up to
`max_replan_attempts` total attempts; after that the cycle stops with
`error.code=replan_exhausted` and still produces an honest Output
Contract response so the user gets a real answer instead of a stack
trace.
"""
from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from typing import Literal as _Literal
from core.replan import FailureType as ReplanCode  # single source of truth

from core.approval import ApprovalProvider
from core.data_classifier import DataClass, SourceHint, classify
from core.evidence import (
    Evidence,
    ProvenanceChain,
    evidence_from_memory_record,
    evidence_from_tool_result,
    make_evidence,
)
from core.ids import new_id
from core.llm import LLM
from core.logger import TraceLogger
from core.memory import WorkingMemory
from core.memory_policy import (
    MemoryRetrievalPolicy,
    MemoryWriteDecision,
    MemoryWritePolicy,
)
from core.memory_echo_antibody import make_event

if TYPE_CHECKING:
    from core.memory_echo_antibody import MemoryWriteEvent, MemoryWriteRegistry
from core.models import (
    Action,
    ApprovalRequest,
    ErrorObject,
    Goal,
    MemoryRecord,
    Observation,
    Plan,
    PlanStep,
    PolicyDecision,
    ToolCall,
    ToolResult,
)
from core.output_policy import apply_ranker_output_policy
from core.low_evidence_policy import evaluate_low_evidence_policy
from core.persistent_memory import PersistentMemoryStore
from core.planner import LLMPlanner, PlannerOutput
from core.policy import PolicyGate
from core.injection_guard import annotate_suspicious, scan_for_injection

# Thread-local storage for per-step replan triggers.
# _execute_step writes here instead of self._last_step_failure so that
# parallel worker threads each own an isolated slot — no shared-state race.
_step_trigger_tls: threading.local = threading.local()

# Tools that read only from the local workspace (trusted boundary).
# Injection guard is skipped for these — their output cannot be injected
# by an external adversary and false-positives degrade signal quality.
_TRUSTED_INTERNAL_TOOLS: frozenset[str] = frozenset({
    "file_read",
    "list_dir",
    "diff_file",
    "run_tests",
    "read_logs",
})
from core.redaction import (
    collect_pii_findings,
    redact_dlp_text,
    redact_payload,
    scan,
)
from core.model_router import ModelRole, ModelRouter
from core.knowledge_pipeline import KnowledgePipeline, KnowledgePipelineResult, RememberFn
from core.user_profile import UserProfile, UserProfileStore, profile_to_prompt_block
from core.assumption_registry import (  # Layer 5
    AssumptionRegistry,
    AssumptionStore,
    extract_from_plan,
    extract_from_question,
)
from core.knowledge_use_policy import KnowledgeUsePolicy
from core.confidence_gate import ConfidenceGate
from core.reasoning_action_check import check_reasoning_actions
from core.role_router import RoleContext, RoleRouter
from core.step_repetition import StepRepetitionTracker
from core.termination_guard import TerminationGuard
from core.smart_memory import (
    EpisodicMemoryStore,
    MemoryConsolidationStore,
    ProceduralMemoryStore,
    consolidate_memory,
    episode_from_agent_cycle,
    format_experience_context,
)
from core.source_ranker import SourceRankingReport, rank_chain
from core.source_registry import SourceRegistry
from core.source_registry_store import SourceRegistryStore
from core.task_complexity import can_skip_planner
from tools.base import ToolRegistry
from tools.file_read import MAX_BYTES as FILE_READ_MAX_BYTES


# Maps tool names to data_classifier source hints. Drives the per-tool
# default DataClass (file_read -> private, web_search -> public, …).
_TOOL_SOURCE_HINTS: dict[str, SourceHint] = {
    "file_read": "file",
    "web_search": "web",
}


def _to_text(output: Any) -> str:
    """Stringify a tool output for classification + scanning.

    Tools return heterogeneous shapes (file_read → str, web_search → list).
    We need ONE flat text view to feed the classifier.
    """
    if isinstance(output, str):
        return output
    try:
        return json.dumps(output, ensure_ascii=False, default=str)
    except Exception:
        return str(output)


# Default attempt budget for re-planning. Two replans (3 attempts total)
# is the tradeoff: enough room to recover from a typo or a flaky source,
# not enough to mask a fundamentally wrong plan as "just one more try".
DEFAULT_MAX_REPLAN_ATTEMPTS = 3


# ReplanCode is an alias for FailureType (core/replan.py) — single source of truth.
# Imported above. No local definition needed.


@dataclass(frozen=True)
class ReplanTrigger:
    """Structured record of one failed PlanStep.

    Lives in two places:
      - `AgentLoop._last_step_failure` — scratch slot set by
        `_execute_step` at every early-return; the parent loop drains it
        right after the step returns None.
      - `failure_history` inside `run()` — the cumulative list across
        attempts, formatted into a `<replan_context>` block fed to the
        planner.

    `arguments` are stored verbatim from the failed step. They are
    redacted at log time by `TraceLogger` and again before the planner
    prompt is sent, so a credential pasted in by the LLM into a tool
    argument cannot leak via the replan path either.
    """

    code: ReplanCode
    step_id: str
    tool_name: str | None
    arguments: dict[str, Any]
    reason: str
    attempt: int


# Output Contract (§1 Interface & Communication + §8 Verification).
# The LLM MUST emit this structure so the user gets:
#   - a direct answer
#   - explicit citations to source labels embedded in the evidence
#   - explicit confidence and unverified gaps
SYSTEM_ANSWER = """You are a careful research analyst.

If the user message contains <evidence> blocks, answer STRICTLY from them.
Each evidence block carries a `source="..."` label (e.g. file:..., web:...).

If the user message contains NO <evidence> blocks, the planner decided that
no tools were needed. Answer from your general knowledge, mark every fact with
the special source label [general-knowledge], and set Confidence accordingly
(typically medium or low — never high without evidence).

Output Contract — your reply MUST follow this structure exactly.
CRITICAL: The six section header words (Conclusion, Facts, Sources, Confidence,
Unverified, Safety) MUST appear verbatim in English, exactly as shown below,
regardless of the user's language. Write all content within each section in the
user's language, but keep the header names in English.

Conclusion:
  One or two sentences that directly answer the question.
  Every factual sentence in Conclusion MUST end with a source label in
  square brackets, using the same citation grammar as Facts.

Facts:
  - Bullet list of supporting facts.
  - Each bullet MUST end with its source label in square brackets.
    Use the citation grammar the Verifier understands:
      [file:<workspace/path>]    workspace file content
      [web:<url>]                fetched web page (kind=web_page)
      [search:<query>]           weak search-result pointer
      [test:<cmd>]               pytest result
      [log:<trace_id>]           JSONL audit log event
      [shell:<cmd>]              shell_exec stdout
      [tool:<name>]              generic tool output (current_time, etc.)
      [diff:<path>]              proposed diff preview
      [memory:<record_id>]       long-term memory record
      [user]                     explicit user directive
      [general-knowledge]        no source consulted (signals
                                 the fact is from training data)
    Pick the citation that matches the <evidence source="..."> label
    closest to the fact you are stating. The Verifier downstream will
    REWRITE matched citations to `[verified:<kind>:<source>]` and tag
    uncited claims with `[unverified]` — so always cite, even when
    using prior knowledge (then cite [general-knowledge]).

Sources:
  1. <source label> - <url or file path verbatim from the evidence header,
     or the literal text "general-knowledge" if no evidence was provided>
  2. ...

Confidence: low | medium | high
  - high   = corroborated by at least TWO INDEPENDENT source kinds
             (e.g. file + test result, file + web, file + shell output).
             A file listing (list_dir) and a README from the SAME project
             are NOT independent — they are the same source. Two file:
             sources alone are medium, not high.
  - medium = supported by a single source OR multiple sources of the same
             kind (e.g. only file: sources) OR confident general-knowledge
             answer with no evidence
  - low    = inferred, partial, or uncertain

Unverified:
  What you could NOT confirm from the evidence, or the single word "nothing".

Safety:
  If a <safety_notes> block is provided, summarise it here in plain language
  (one short paragraph per finding). Tell the user which surface the secret
  was found on (user_input / tool_output / final_answer), how many shapes
  were redacted, and that the kernel — not the model — performed the
  redaction. If no <safety_notes> block is provided, write the single word
  "nothing".

Hard rules:
- NEVER translate or reword the six section headers. They MUST be exactly:
  "Conclusion:", "Facts:", "Sources:", "Confidence:", "Unverified:", "Safety:"
  Do NOT write "Заключение:", "Факты:", "Вывод:", or any other variant.
  Use plain bold or no decoration — do NOT use markdown `#` headings for
  section headers. The content inside each section is in the user's language;
  the header words are fixed English structural markers.
- NEVER invent facts, URLs, or sources not present in the evidence.
- If the evidence does not answer the question, say so in Conclusion AND list
  the unanswered parts under Unverified.
- Quote URLs and file paths verbatim from the evidence `source` attributes.
- If an <allowed_citations> block is present, cite ONLY those exact bracketed
  citation tokens. Do not cite raw <evidence source="..."> labels unless they
  also appear in <allowed_citations>.
- NEVER reproduce any [REDACTED:*] token's underlying value, even if you
  can guess it; treat the token as the actual content.
- NEVER repeat or paraphrase the user's question back to them.
  The Conclusion must ANSWER, not restate. Wrong: "You asked about X, X is..."
  Right: directly state the answer.
- NEVER use technical jargon or system-level terms in the Conclusion or Facts
  unless the user's question itself was technical.
- When you cannot perform an action (PDF, DOCX, rendering, running code), explain
  WHY precisely — distinguish between:
  (a) "requires paid software" (Microsoft Office, Adobe Acrobat) — name it and say
      a free alternative exists (LibreOffice, python-docx, etc.)
  (b) "requires a library to be installed" — name the library (pip install X)
  (c) "genuinely outside my capabilities" (display a GUI, play audio, etc.)
  Never say "unavailable" or "no tools" without specifying which tool or licence is missing.

User Profile Guidance (P2 — style only):
- If a <user_profile> block is present, use it ONLY for presentation:
    * verbosity (brief / normal / detailed) — how long the answer is.
    * vocabulary (technical / plain) — terminology depth.
    * expertise (novice / intermediate / expert) — explanation depth.
    * language — the language to answer in.
- The <user_profile> block MUST NOT influence:
    * which topic or domain you cover,
    * which sources or evidence you trust,
    * what the answer is "really about".
  The CURRENT QUESTION alone defines scope. Past interaction history is
  not a constraint on what may be asked now. Answer the question that
  was asked, fully, using the available evidence — even if the topic
  is outside the user's typical domain.
"""

# §3.x — register this prompt with the global Prompt Registry
try:
    from core.prompt_registry import register_prompt as _rp
    _rp("synthesizer.system", SYSTEM_ANSWER, module="core.loop",
        description="Output contract for the LLM synthesizer (§3 Cognitive Core)")
except ImportError:  # pragma: no cover
    pass

# Regex that matches the internal verification markers the Verifier inlines
# into the answer text.  These are audit annotations, not user content.
# Stripped before the answer leaves the kernel so users never see them.
_VERIF_MARKER_RE = re.compile(
    r"\s*\["
    r"(?:unverified"
    r"|verified:[^\]]*"
    r"|declared:[^\]]*"
    r")\]",
    re.IGNORECASE,
)


def _strip_verification_markers(text: str) -> str:
    """Remove inline verification markers from user-facing answer text."""
    return _VERIF_MARKER_RE.sub("", text)


# Regex that strips source citation tokens from individual sentences/bullets.
# Matches: [general-knowledge] [web:url] [file:path] [search:q] [test:cmd]
# [log:id] [shell:cmd] [diff:p] [memory:id] [user] [declared:...] etc.
_ANSWER_CITATION_RE = re.compile(
    r"\s*\[(?:general-knowledge|web:[^\]]*|file:[^\]]*|file_write:[^\]]*|"
    r"file_read:[^\]]*|search:[^\]]*|"
    r"test:[^\]]*|log:[^\]]*|shell:[^\]]*|diff:[^\]]*|memory:[^\]]*|"
    r"user|declared:[^\]]*|verified:[^\]]*|unverified(?::[^\]]*)?)"
    r"(?:\s*;\s*[^\]]*)?\]",
    re.IGNORECASE,
)


def format_human_response(answer: str) -> str:
    """Convert the internal Output Contract format to clean human-readable text.

    Strips: section headers (Conclusion/Facts/Sources/Confidence/Safety/Unverified),
    source citation tokens ([general-knowledge], [web:...], etc.),
    and internal [note] disclaimers.

    Keeps: the actual content — conclusion sentences + fact bullets —
    formatted as natural prose.  If the answer is NOT in Output Contract
    format (no "Conclusion:" header), returns the text unchanged.
    """
    if "Conclusion:" not in answer and "conclusion:" not in answer:
        return answer  # not an Output Contract reply — return as-is

    lines = answer.splitlines()
    section: str | None = None
    conclusion_lines: list[str] = []
    facts_lines: list[str] = []
    unverified_lines: list[str] = []

    _SKIP_PREFIXES = ("sources:", "confidence:", "safety:", "[note]")

    for raw in lines:
        stripped = raw.strip()
        low = stripped.lower()

        # ── section detection ─────────────────────────────────────────────
        if low.startswith("conclusion:"):
            section = "conclusion"
            rest = stripped[len("conclusion:"):].strip()
            if rest:
                conclusion_lines.append(rest)
            continue
        if low.startswith("facts:"):
            section = "facts"
            continue
        if low.startswith("unverified:"):
            section = "unverified"
            rest = stripped[len("unverified:"):].strip()
            rest_norm = rest.rstrip(".,!;:").lower()
            if rest and rest_norm not in ("nothing", "ничего", "нет", "нет данных"):
                unverified_lines.append(rest)
            continue
        if any(low.startswith(p) for p in _SKIP_PREFIXES):
            section = "skip"
            continue

        # ── content collection ────────────────────────────────────────────
        if section == "conclusion":
            if stripped:
                conclusion_lines.append(stripped)

        elif section == "facts":
            if not stripped:
                continue
            # Bold subheader like **Сбор данных:**
            if stripped.startswith("**") and (stripped.endswith("**") or stripped.endswith(":**")):
                label = stripped.strip("*").rstrip(":").strip()
                if label:
                    facts_lines.append(f"\n{label}:")
                continue
            # Bullet line
            if stripped[:2] in ("- ", "• ", "* "):
                clean = _ANSWER_CITATION_RE.sub("", stripped[2:]).strip()
                clean = clean.replace("**", "")
                if clean:
                    facts_lines.append(f"• {clean}")
            else:
                # Continuation text inside facts (e.g. under a bold subheader)
                clean = _ANSWER_CITATION_RE.sub("", stripped).strip()
                if clean:
                    facts_lines.append(f"  {clean}")

        elif section == "unverified":
            normalized = stripped.rstrip(".,!;:").lower()
            if stripped and normalized not in ("nothing", "ничего", "нет", "нет данных"):
                # Strip inline citation tokens that bleed into unverified text
                clean = _ANSWER_CITATION_RE.sub("", stripped).strip()
                if clean and clean.rstrip(".,!;:").lower() not in ("nothing", "ничего", "нет", "нет данных"):
                    unverified_lines.append(clean)

    # ── assemble ──────────────────────────────────────────────────────────
    def _clean(text: str) -> str:
        return _ANSWER_CITATION_RE.sub("", text).strip()

    conclusion = " ".join(_clean(l) for l in conclusion_lines if l.strip()).strip()
    facts_block = "\n".join(facts_lines).strip()

    parts: list[str] = []
    if conclusion:
        parts.append(conclusion)
    if facts_block:
        parts.append(facts_block)
    if unverified_lines:
        note = " ".join(unverified_lines)
        parts.append(f"⚠️ Не подтверждено: {note}")

    return "\n\n".join(parts) if parts else answer


class AgentLoop:
    """Runs a single agent cycle.

    MVP-3: the planner is an LLM. The CLI only supplies the question plus an
    optional file hint. The planner picks which tools (if any) to call; the
    Executor runs the plan and the Synthesizer produces the Output Contract.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        policy: PolicyGate,
        llm: LLM,
        logger: TraceLogger,
        planner: LLMPlanner | None = None,
        model_router: ModelRouter | None = None,
        memory: WorkingMemory | None = None,
        persistent_store: PersistentMemoryStore | None = None,
        retrieval_policy: MemoryRetrievalPolicy | None = None,
        write_policy: MemoryWritePolicy | None = None,
        memory_write_registry: "MemoryWriteRegistry | None" = None,
        role_router: RoleRouter | None = None,
        knowledge_use_policy: KnowledgeUsePolicy | None = None,
        source_registry_store: SourceRegistryStore | None = None,
        knowledge_pipeline: KnowledgePipeline | None = None,
        episodic_store: EpisodicMemoryStore | None = None,
        procedural_store: ProceduralMemoryStore | None = None,
        consolidation_store: MemoryConsolidationStore | None = None,
        knowledge_auto_write: bool = False,
        approval_provider: ApprovalProvider | None = None,
        max_replan_attempts: int = DEFAULT_MAX_REPLAN_ATTEMPTS,
        replan_policy: "ReplanPolicy | None" = None,
        verifier_enabled: bool = True,
        clarification_enabled: bool = True,
        clarification_gate_enabled: bool = True,
        odd_enabled: bool = True,
        cheap_path_enabled: bool = True,
        user_profile_store: UserProfileStore | None = None,
        assumption_store: AssumptionStore | None = None,  # Layer 5
    ):
        self.registry = registry
        self.policy = policy
        self.model_router = model_router or ModelRouter.single(llm)
        self.llm = self.model_router.for_role(ModelRole.SYNTHESIZER)
        self.log = logger
        self.planner = planner or LLMPlanner(
            llm=self.model_router.for_role(ModelRole.PLANNER),
            registry=registry,
        )
        self.memory = memory  # may be None for stateless one-shot use
        self.persistent_store = persistent_store
        self.retrieval_policy = retrieval_policy or MemoryRetrievalPolicy()
        self.write_policy = write_policy or MemoryWritePolicy()
        self.memory_write_registry = memory_write_registry
        self.role_router = role_router or RoleRouter()
        self.knowledge_use_policy = knowledge_use_policy or KnowledgeUsePolicy()
        self.source_registry_store = source_registry_store
        self.knowledge_pipeline = knowledge_pipeline or KnowledgePipeline()
        self.episodic_store = episodic_store
        self.procedural_store = procedural_store
        self.consolidation_store = consolidation_store
        self.knowledge_auto_write = bool(knowledge_auto_write)
        # When None, escalated actions are blocked outright (safe default).
        self.approval_provider = approval_provider
        # MVP-12: replan policy decides per-FailureType budgets, advises
        # the planner, and tracks forbidden (tool, args) pairs that must
        # not be retried after approval_deny / policy_blocked etc.
        #
        # Backward compatibility: the older `max_replan_attempts` parameter
        # is still honoured. If the caller supplied an explicit policy we
        # use it as-is; otherwise we synthesise the default policy with the
        # caller's global cap. Mismatch is an error — we refuse to silently
        # ignore one of the two knobs.
        if max_replan_attempts < 1:
            raise ValueError(
                f"max_replan_attempts must be >= 1, got {max_replan_attempts}"
            )
        from core.replan import (
            ReplanPolicy as _ReplanPolicyCls,
        )

        if replan_policy is None:
            self.replan_policy = _ReplanPolicyCls(
                max_total_replans=max_replan_attempts
            )
        else:
            if (
                max_replan_attempts != DEFAULT_MAX_REPLAN_ATTEMPTS
                and max_replan_attempts != replan_policy.max_total_replans
            ):
                raise ValueError(
                    "Pass either `max_replan_attempts` OR `replan_policy`, "
                    "not both with conflicting caps "
                    f"(max_replan_attempts={max_replan_attempts}, "
                    f"replan_policy.max_total_replans="
                    f"{replan_policy.max_total_replans})"
                )
            self.replan_policy = replan_policy
        # Keep the legacy attribute for any external code reading it.
        self.max_replan_attempts = self.replan_policy.max_total_replans
        # MVP-11 Compensation registry. Every successful tool call that
        # carries a non-noop CompensationPlan in its structured output
        # gets appended here, LIFO. `rollback()` pops the last plan and
        # applies it via `core.compensation.apply_compensation_plan`.
        # Persistence is intentionally in-memory: rollback must be done
        # WITHIN the session that produced the change (a future MVP can
        # serialise the log if cross-session undo proves useful).
        from core.compensation import CompensationPlan  # avoid top-level cycle

        self._CompensationPlanCls = CompensationPlan
        self.compensation_log: list[CompensationPlan] = []
        # Per-cycle scratch list, populated by `_execute_step` whenever a
        # secret is found in a tool output. `run()` resets it on entry and
        # passes it into the synthesizer so the answer can mention any
        # redaction action it took.
        self._cycle_findings: list[dict[str, Any]] | None = None
        # Per-step scratch slot. `_execute_step` writes a `ReplanTrigger`
        # into this every time it bails out with `None`. The parent loop
        # in `run()` consumes it and clears it. Keeping it as instance
        # state mirrors the `_cycle_findings` pattern and avoids
        # restructuring the return type of `_execute_step`.
        self._last_step_failure: ReplanTrigger | None = None
        # Current attempt number — set by `run()` at the top of each
        # iteration so `_execute_step` can stamp failure triggers with
        # the right attempt index without taking a new parameter.
        self._current_attempt: int = 1
        # Synthesis streaming callback — set by `run()` when the caller
        # passes `on_token`; read by `_synthesize()`. Reset to None at
        # the end of each cycle so leaking across turns is impossible.
        self._stream_on_token: Any = None
        # MVP-14.1 — provenance scratch state. `_retrieve_persistent`
        # stashes the records it injected so `run()` can fold them into
        # the Evidence chain. `last_provenance` is set at the end of
        # each `run()` and exposed for tests / the upcoming Verifier.
        self._last_persistent_records: list[Any] = []
        self._last_episode_records: list[Any] = []
        self._last_procedure_records: list[Any] = []
        # Fast-path cache: best episodic match from the last _retrieve_experience_memory call.
        # Keyed so run() can serve a cached answer without touching the LLM.
        self._last_best_similar_episode: Any = None  # EpisodeRecord | None
        self._last_best_similar_score: float = 0.0
        # Planner plan cache: skips LLM planner call for identical questions
        # within the same session (invalidated when the episodic store changes).
        # Key: (hash(question), episodic_mtime, file_hint)
        self._planner_cache: dict[tuple[int, float, str], Any] = {}
        # MAST FM-1.3 step-repetition tracker; replaced per `run()` call.
        self._step_repetition: StepRepetitionTracker = StepRepetitionTracker()
        # MAST FM-1.5 / FM-3.1 termination guard; replaced per `run()` call.
        self._termination_guard: TerminationGuard = TerminationGuard()
        # Post-verifier confidence gate (Berkeley MAST 2025, Horvitz 1999).
        # Constructed once; threshold/min_total can be overridden by tests.
        self._confidence_gate: ConfidenceGate = ConfidenceGate()
        self.last_provenance: ProvenanceChain = ProvenanceChain()
        self.last_role_context: RoleContext = self.role_router.route("")
        # MVP-14.4 — Verifier wiring. `verifier_enabled=False` skips the
        # annotation pass; the draft answer is returned verbatim. Useful
        # for legacy tests and for callers who want raw LLM output.
        self.verifier_enabled = bool(verifier_enabled)
        # Layer 4 — User Profile store and last-known profile snapshot.
        self.user_profile_store = user_profile_store
        self.last_user_profile: UserProfile | None = None
        # Layer 5 — Assumption Registry store and last-run snapshot.
        self.assumption_store = assumption_store
        self.last_assumptions: AssumptionRegistry | None = None
        self._run_assumptions_current: AssumptionRegistry | None = None
        # §3 Clarification Policy wiring. `clarification_enabled=False` skips
        # the heuristic ambiguity check. Intended ONLY for integration tests
        # that exercise the tool/approval layer with synthetic short questions.
        self.clarification_enabled = bool(clarification_enabled)
        # B-1 Clarification Gate wiring (режим переспроса). When the loop
        # gets STUCK (replan exhausted == loop_suspected), switch to
        # "ask, don't build": prepend the minimal clarifying questions to the
        # honest failure answer so the operator can narrow the frame instead of
        # the agent churning. `False` skips it for tests that assert on the bare
        # exhaustion answer. Pure/deterministic — no LLM, no I/O.
        self.clarification_gate_enabled = bool(clarification_gate_enabled)
        # §7 Operational Design Domain (ODD / B-05) wiring. When enabled, an
        # out-of-domain request is refused or escalated BEFORE any planning.
        # `odd_enabled=False` skips the check for integration tests.
        self.odd_enabled = bool(odd_enabled)
        # Cheap-path planner gate (TD — trivial no-tool input skips the planner
        # LLM call and synthesises directly). `False` forces the planner to run
        # for every input, preserving the pre-gate behaviour for tests/rollback.
        self.cheap_path_enabled = bool(cheap_path_enabled)
        from core.verifier import VerificationReport as _VR

        self.last_verification: _VR | None = None
        # MVP-14.3/14.3x — trust metadata over the Evidence chain.
        # Source ranking is logged/exposed, and Ranker-to-Output Policy uses
        # it to cap confidence for unsuitable realtime sources.
        self.last_source_ranking: SourceRankingReport | None = None
        # Source registry is the catalog view over the same chain:
        # source records plus first-pass claims extracted from Evidence.
        self.last_source_registry: SourceRegistry = SourceRegistry()
        self.last_knowledge_pipeline: KnowledgePipelineResult | None = None

    # ---------- public entry point ----------

    def run(
        self,
        user_question: str,
        file_hint: str | None = None,
        on_token: Any = None,
        deep_escalation: Any = None,
    ) -> str:
        """Run one observe→plan→act→verify→respond cycle.

        Args:
            user_question: The user's natural-language input.
            file_hint: Optional workspace file path to pre-load.
            on_token: Optional ``(str) -> None`` callback invoked for each
                      synthesis token as it streams from the LLM.  Pass
                      ``lambda t: print(t, end="", flush=True)`` for live
                      CLI display.  ``None`` (default) disables streaming.
            deep_escalation: Optional operator-supplied
                      :class:`~core.deep_escalation.OperatorEscalation`. Only an
                      explicit, valid operator reason lets planner/synthesizer
                      escalate to the deep (Opus) tier; the default ``None``
                      keeps every autonomous run on the standard tier.
        """
        # Store streaming callback so _synthesize() can pick it up without
        # changing its signature (which is called from multiple paths).
        self._stream_on_token = on_token
        self._cycle_findings = []
        self.last_source_ranking = None
        self.last_source_registry = SourceRegistry()
        self.last_knowledge_pipeline = None

        # Layer 4 — load the user profile for this cycle.
        if self.user_profile_store is not None:
            self.last_user_profile = self.user_profile_store.load_or_default()
            self.log.log(
                "user_profile_load",
                {
                    "expertise": self.last_user_profile.expertise,
                    "verbosity": self.last_user_profile.verbosity,
                    "language": self.last_user_profile.language,
                    "interaction_count": self.last_user_profile.interaction_count,
                    "interests": self.last_user_profile.interests,
                },
            )

        # Layer 5 — create a fresh AssumptionRegistry and seed it from the question.
        # Layer 4→5 bridge: pass the profile's known language so extract_from_question
        # uses a higher-confidence profile signal instead of a raw heuristic.
        _run_assumptions = AssumptionRegistry(
            run_id=getattr(self.log, "trace_id", ""),
        )
        # Layer 2→5 bridge: if the store already has assumptions for this run_id
        # (e.g. a previous failed attempt in the same session), restore them so
        # they are not duplicated and the prompt block stays accurate.
        try:
            if self.assumption_store is not None:
                _existing = self.assumption_store.load_by_run(
                    getattr(self.log, "trace_id", "")
                )
                if _existing:
                    _run_assumptions.restore_from_store(_existing)
                    self.log.log("assumptions_restored", {
                        "run_id": getattr(self.log, "trace_id", ""),
                        "count": len(_existing),
                    })
        except Exception:
            pass  # Restore must never abort the run.
        try:
            _known_lang: str | None = None
            if self.last_user_profile is not None:
                _known_lang = self.last_user_profile.language or None
            _q_assumptions = extract_from_question(
                user_question,
                run_id=getattr(self.log, "trace_id", ""),
                known_language=_known_lang,
            )
            _run_assumptions.register_many(_q_assumptions)
        except Exception:
            pass  # Assumption extraction must never abort the run.

        # §3.5 Checkpoint writer — one file per trace, append-only.
        # Falls back to a no-op sentinel when the logger is a test spy that
        # does not expose trace_id / log_dir (avoids coupling tests to I/O).
        try:
            from core.checkpoint import CheckpointWriter as _CPWriter
            _cp: Any = _CPWriter(trace_id=self.log.trace_id, log_dir=self.log.log_dir)
        except (AttributeError, ValueError):
            class _NoOpCP:  # noqa: N801
                """Silently drops all checkpoint calls."""
                def save_observe(self, **_kw: Any) -> None: pass
                def save_plan(self, **_kw: Any) -> None: pass
                def save_act(self, **_kw: Any) -> None: pass
                def save_respond(self, **_kw: Any) -> None: pass
            _cp = _NoOpCP()

        # 1. Observe
        observation = Observation(
            source="cli",
            modality="text",
            content={
                "question": user_question,
                "file_hint": file_hint,
            },
            provenance="user",
        )
        self.log.log("observe", observation)
        _cp.save_observe(question=user_question, file_hint=file_hint)

        # Classify the question itself. A user can paste a secret into the
        # prompt; the kernel must catch it BEFORE the LLM sees it.
        q_cls = classify(user_question, source="cli")
        self.log.log(
            "data_classified",
            {
                "label": "user_question",
                "class": q_cls.cls.value,
                "source": q_cls.source,
                "reasons": q_cls.reasons,
            },
        )
        if q_cls.cls == DataClass.SECRET:
            q_findings = scan(user_question)
            kinds = sorted({f.kind for f in q_findings})
            self.log.log(
                "secret_detected",
                {
                    "label": "user_question",
                    "kinds": kinds,
                    "count": len(q_findings),
                    "surface": "user_input",
                },
            )
            self._cycle_findings.append(
                {"label": "user_question", "kinds": kinds, "count": len(q_findings)}
            )
        elif q_cls.cls == DataClass.SENSITIVE:
            q_findings = collect_pii_findings(user_question)
            kinds = sorted({f"pii-{f.kind}" for f in q_findings})
            self.log.log(
                "sensitive_detected",
                {
                    "label": "user_question",
                    "kinds": kinds,
                    "count": len(q_findings),
                    "surface": "user_input",
                },
            )
            self._cycle_findings.append(
                {"label": "user_question", "kinds": kinds, "count": len(q_findings)}
            )

        self.last_role_context = self.role_router.route(user_question)
        self.log.log("role_route", self.last_role_context.to_log_payload())

        # Adaptive model routing: choose LLM tier based on task complexity.
        # LIGHT tasks (greetings, quick lookups) get a cheap/fast model.
        # DEEP tasks (architecture, full audits, from-scratch builds) get
        # the most powerful available model (opus, o1, …).
        # STANDARD tasks reuse the default role route — no change.
        # Falls back silently to the default LLM if for_task() raises.
        try:
            _task_planner_llm = self.model_router.for_task(
                ModelRole.PLANNER, user_question, escalation=deep_escalation
            )
            _task_synth_llm = self.model_router.for_task(
                ModelRole.SYNTHESIZER, user_question, escalation=deep_escalation
            )
            _planner_model = getattr(_task_planner_llm, "model", None)
            _synth_model = getattr(_task_synth_llm, "model", None)
            _route_reason = getattr(
                getattr(_task_planner_llm, "route", None), "reason", "default"
            )
            self.log.log(
                "adaptive_route",
                {
                    "question_chars": len(user_question),
                    "planner_model": _planner_model,
                    "synth_model": _synth_model,
                    "route_reason": _route_reason,
                },
            )
        except Exception:
            _task_planner_llm = None
            _task_synth_llm = None

        # 2. Interpret -> Goal
        goal = self._interpret(observation)
        self.log.log("interpret", goal)

        # 2b. Operational Design Domain gate (§7 ODD / B-05).
        # Pure-heuristic check — no LLM, no I/O.  When the request falls
        # outside the agent's operational domain (harmful/illegal, real
        # money, physical world, regulated advice, authority over people),
        # the loop stops here BEFORE any planning and returns an honest
        # refusal/escalation message instead of improvising an action.
        if self.odd_enabled:
            _odd = self._check_operational_domain(user_question)
            if _odd.blocks:
                self.log.log(
                    "out_of_domain",
                    {
                        "verdict": _odd.verdict,
                        "action": _odd.action,
                        "findings": [
                            {"kind": f.kind, "evidence": f.evidence, "confidence": f.confidence}
                            for f in _odd.findings
                        ],
                    },
                )
                self._stream_on_token = None
                return _odd.message

        # 2c. Clarification Policy (§3 Clarification Policy).
        # Pure-heuristic check — no LLM, no I/O.  When the question is
        # ambiguous about a destructive action the loop stops here and
        # returns the clarification question to the caller so the REPL
        # can surface it before any planning starts.
        if self.clarification_enabled:
            _clarif = self._check_clarification(user_question)
            if _clarif.should_ask:
                self.log.log(
                    "clarification_request",
                    {
                        "question": _clarif.question,
                        "findings": [
                            {"kind": f.kind, "evidence": f.evidence, "confidence": f.confidence}
                            for f in _clarif.findings
                        ],
                    },
                )
                self._stream_on_token = None
                return _clarif.question

        # Memory retrieval — read-only injection into prompts
        history = ""
        if self.memory is not None:
            history = self.memory.conversation_context(max_turns=5)
            if history:
                self.log.log(
                    "memory_inject",
                    {
                        "session_id": self.memory.session_id,
                        "turns_visible": len(self.memory.recent_turns(5)),
                        "history_chars": len(history),
                        "artifacts_cached": len(self.memory.artifacts),
                    },
                )

        # Persistent memory retrieval — pick a few long-term records that
        # share keywords with the question, then format them as a
        # <long_term_memory> block injected into planner + synthesizer.
        persistent_block = self._retrieve_persistent(user_question)
        experience_block = self._retrieve_experience_memory(user_question)

        # ── Episodic fast path ───────────────────────────────────────────────────
        # Jaccard ≥ 0.85 AND quality ≥ 0.70 → serve the stored answer directly,
        # skipping both the planner LLM call and the synthesizer LLM call.
        # Conditions that disable the fast path:
        #   - file_hint is set (the answer is tied to a specific file)
        #   - question starts with ':' (operator command)
        #   - full_answer is empty (episode from before this feature)
        #   - the cached episode used tools — its answer depends on the state of
        #     the environment (files, installed packages, command output) which
        #     may have changed since; only purely reasoned answers (no tools)
        #     are safe to replay verbatim.
        _fp_ep = self._last_best_similar_episode
        _fp_score = self._last_best_similar_score
        if (
            not file_hint
            and not user_question.strip().startswith(":")
            and _fp_ep is not None
            and _fp_score >= 0.85
            and _fp_ep.answer_quality_score >= 0.70
            and _fp_ep.full_answer
            and not _fp_ep.tools_used
        ):
            self.log.log(
                "episodic_fast_path",
                {
                    "episode_id": _fp_ep.id,
                    "similarity": round(_fp_score, 4),
                    "quality": round(_fp_ep.answer_quality_score, 4),
                    "answer_chars": len(_fp_ep.full_answer),
                },
            )
            self._record_experience_memory(
                goal_description=goal.description,
                question=user_question,
                answer=_fp_ep.full_answer,
                tools_used=[],
                source_labels=[f"memory:{_fp_ep.id}"],
                verified_chunks=1,
                unverified_chunks=0,
                replan_exhausted=False,
            )
            if self.memory is not None:
                self.memory.record_turn(
                    question=user_question,
                    planner_reasoning="episodic fast path — cached answer",
                    tools_used=[],
                    artifact_labels=[f"memory:{_fp_ep.id}"],
                    answer=_fp_ep.full_answer,
                )
            self._stream_on_token = None
            return _fp_ep.full_answer

        # Planner sees the persistent block prepended to working history so
        # it can opt out of redundant tool calls when the answer is already
        # in long-term memory. Role is logged and injected into synthesis,
        # but kept out of `history` so `<conversation_history>` stays a
        # strict marker for actual prior dialogue.
        planner_history = "\n\n".join(
            part for part in (persistent_block, experience_block, history) if part.strip()
        )
        multi_file = self._prepare_multi_file_review(user_question, file_hint=file_hint)
        if multi_file["kind"] == "refusal":
            answer = str(multi_file["message"])
            self.log.log("multi_file_review_refused", multi_file)
            self.log.log(
                "respond",
                {
                    "chars": len(answer),
                    "sources": [f"file:{file_hint}"] if file_hint else [],
                    "redactions": len(self._cycle_findings or []),
                    "attempts_used": 0,
                    "replan_exhausted": False,
                },
            )
            if self.memory is not None:
                turn = self.memory.record_turn(
                    question=user_question,
                    planner_reasoning="kernel multi-file review refusal",
                    tools_used=[],
                    artifact_labels=[],
                    answer=answer,
                )
                self.log.log(
                    "memory_write",
                    {
                        "session_id": self.memory.session_id,
                        "turn_id": turn.id,
                        "turn_index": turn.index,
                        "tools_used": turn.tools_used,
                        "labels": turn.artifact_labels,
                    },
                )
            self._record_experience_memory(
                goal_description=goal.description,
                question=user_question,
                answer=answer,
                tools_used=[],
                source_labels=[f"file:{file_hint}"] if file_hint else [],
                verified_chunks=0,
                unverified_chunks=1,
                replan_exhausted=False,
            )
            self._stream_on_token = None
            return answer
        forced_sources = (
            list(multi_file["sources"])
            if multi_file["kind"] == "forced"
            else None
        )
        forced_reasoning = str(multi_file.get("reasoning") or "")
        forced_warnings = list(multi_file.get("warnings") or [])

        # 3. Plan + 4. Act + 5. Observe Result + 6. Verify, wrapped in a
        # bounded re-planning loop. On every iteration:
        #   - build a planner prompt (with <replan_context> after the first
        #     attempt)
        #   - run every step and collect artifacts
        #   - if the plan was non-empty AND no artifact survived, this
        #     attempt failed; promote `failure_history` and try again
        #   - stop on success OR when the attempt budget is gone
        failure_history: list[ReplanTrigger] = []
        artifacts: dict[str, dict[str, Any]] = {}
        # Per-run step repetition tracker (MAST FM-1.3). Counts (tool, args)
        # executions across all attempts so the loop can surface looping
        # planners. Reset every `run()` call.
        self._step_repetition = StepRepetitionTracker()
        # Per-run termination guard (MAST FM-1.5, FM-3.1).
        self._termination_guard = TerminationGuard()
        # MVP-14.1 — typed Evidence chain. Built in parallel with
        # `artifacts`; lives at the same scope so the synthesizer (and,
        # later, the Verifier) can consult it.
        chain: ProvenanceChain = ProvenanceChain()
        planner_out: PlannerOutput | None = None
        plan: Plan | None = None
        replan_exhausted = False
        # Cheap-path cost gate: set True only when the planner-skip branch
        # below fires for a trivial no-tool turn. Downstream this trims the
        # synthesizer context, forces the LIGHT (cheap) model tier and skips
        # the per-turn knowledge pipeline + memory consolidation — none of
        # which add value for a one-line greeting / config-flag echo.
        cheap_path_active = False
        # MVP-12: advice + forbidden-actions list carried over from the
        # previous attempt's policy.decide() call. Empty on the first
        # attempt; populated on every replan.
        advice_for_planner: str = ""
        forbidden_actions: tuple[tuple[str, str], ...] = ()

        attempt = 0
        while True:
            attempt += 1
            self._current_attempt = attempt

            # Emit a structured `replan_attempt` event for every loop
            # iteration AFTER the first — makes it trivial for tests
            # (and humans) to see how many attempts ran and which advice
            # the planner saw on each.
            if attempt > 1:
                self.log.log(
                    "replan_attempt",
                    {
                        "attempt": attempt,
                        "max_total": self.replan_policy.max_total_replans,
                        "advice_chars": len(advice_for_planner),
                        "forbidden_action_count": len(forbidden_actions),
                        "failure_counts_so_far": dict(
                            self._count_failures(failure_history)
                        ),
                    },
                )

            failure_context = self._format_replan_context(
                failure_history,
                attempt,
                self.replan_policy.max_total_replans,
                advice=advice_for_planner,
                forbidden_actions=forbidden_actions,
            )

            if attempt == 1 and forced_sources is not None:
                planner_out = PlannerOutput(
                    reasoning=forced_reasoning,
                    sources=forced_sources,
                    raw_response="",
                    warnings=forced_warnings,
                )
            else:
                # ── Cheap-path gate ───────────────────────────────────────
                # Trivial, no-tool input (config-flag echoes, greetings, one
                # line "what is X") never needs a tool: the planner would only
                # spend a full LLM call to return an empty plan. Skip that call
                # and let the normal empty-plan flow synthesise the answer.
                # Gated to the first attempt with no failure/replan context so
                # replans always get the real planner.
                if (
                    self.cheap_path_enabled
                    and attempt == 1
                    and not failure_context.strip()
                    and can_skip_planner(user_question, file_hint=file_hint)
                ):
                    planner_out = PlannerOutput(
                        reasoning=(
                            "Cheap path: trivial no-tool input — answering from "
                            "general knowledge and memory without a planner call."
                        ),
                        sources=[],
                        raw_response="",
                        warnings=["planner_skipped_cheap_path"],
                        diagnostics={
                            "stage": "skipped",
                            "reason": "trivial no-tool input",
                            "fallback": "cheap_path",
                        },
                    )
                    self.log.log(
                        "planner_cheap_path",
                        {
                            "question_chars": len(user_question),
                            "reason": "trivial no-tool input",
                        },
                    )
                    cheap_path_active = True
                else:
                    # ── Planner cache ─────────────────────────────────────
                    # Cache key: (question hash, episodic store mtime, file_hint).
                    # Mtime invalidates the cache whenever a new episode is written
                    # (the store changes → the planner might choose different tools).
                    # Only applied on the first attempt with no failure context.
                    _pc_key = (
                        hash(user_question.lower().strip()),
                        self._episodic_store_mtime(),
                        file_hint or "",
                    )
                    if (
                        attempt == 1
                        and not failure_context.strip()
                        and _pc_key in self._planner_cache
                    ):
                        planner_out = self._planner_cache[_pc_key]
                        self.log.log(
                            "planner_cache_hit",
                            {
                                "key_hash": _pc_key[0],
                                "tools_cached": [s["tool"] for s in planner_out.sources],
                            },
                        )
                    else:
                        planner_out = self.planner.plan(
                            question=user_question,
                            file_hint=file_hint,
                            history=planner_history,
                            failure_context=failure_context,
                            forbidden_actions=forbidden_actions,
                            llm=_task_planner_llm,
                        )
                        if (
                            attempt == 1
                            and not failure_context.strip()
                            and "plan_parse_failed" not in planner_out.warnings
                        ):
                            self._planner_cache[_pc_key] = planner_out
                planner_out = self._force_file_hint_read_when_explicit(
                    planner_out,
                    question=user_question,
                    file_hint=file_hint,
                )
            self.log.log(
                "planner",
                {
                    "reasoning": planner_out.reasoning,
                    "tools_chosen": [s["tool"] for s in planner_out.sources],
                    "warnings": planner_out.warnings,
                    "raw_chars": len(planner_out.raw_response),
                    "attempt": attempt,
                    "replan_context_chars": len(failure_context),
                },
            )
            # Fix #2: surface planner hallucinations as a dedicated event.
            # When the LLM invents a tool name that is not in the registry,
            # _validate_steps drops the step silently.  If every step was
            # dropped (plan_empty_after_drop=True) the loop will proceed to
            # synthesise from general knowledge — making the answer look
            # confident when the actual plan failed.  This event lets
            # operators detect the failure mode without parsing warning strings.
            if planner_out.dropped_tools:
                self.log.log(
                    "plan_tool_drop",
                    {
                        "dropped": planner_out.dropped_tools,
                        "plan_empty_after_drop": not planner_out.sources,
                        "attempt": attempt,
                    },
                )

            # MAST FM-2.6 — reasoning ↔ action consistency check.
            try:
                _ra_report = check_reasoning_actions(
                    planner_out.reasoning,
                    [s["tool"] for s in planner_out.sources],
                )
                if _ra_report.has_mismatch:
                    self.log.log(
                        "reasoning_action_mismatch",
                        {
                            **_ra_report.to_log_payload(),
                            "attempt": attempt,
                        },
                    )
            except Exception:
                pass  # Observational only — must never abort the loop.

            plan = self._build_plan(goal, planner_out.sources)
            self.log.log("plan", plan, steps=len(plan.steps), attempt=attempt)
            _cp.save_plan(attempt=attempt, step_ids=[s.id for s in plan.steps])

            # Layer 5 — extract plan-level assumptions on the first attempt only.
            if attempt == 1:
                try:
                    _plan_assumptions = extract_from_plan(
                        planner_out.sources,
                        question=user_question,
                        run_id=getattr(self.log, "trace_id", ""),
                    )
                    _run_assumptions.register_many(_plan_assumptions)
                    if _run_assumptions.assumptions:
                        self.log.log(
                            "assumptions_registered",
                            {
                                "count": len(_run_assumptions),
                                "assumptions": _run_assumptions.to_log_payload(),
                            },
                        )
                except Exception:
                    pass  # Never abort the run.

            attempt_artifacts: dict[str, dict[str, Any]] = {}
            attempt_failures: list[ReplanTrigger] = []
            attempt_chain = ProvenanceChain()

            # Planner JSON parse failure: empty `sources` here is NOT an
            # intentional general-knowledge plan, it's a contract break.
            # Without this gate the loop would fall through to the
            # `if not plan.steps or attempt_artifacts: break` branch
            # below and the synthesizer would happily produce a long
            # confident answer from zero evidence. Treat it as a real
            # failure so `replan_policy.decide()` either gets us a clean
            # JSON retry or trips `replan_exhausted` and the synthesizer
            # writes the honest "I could not plan" reply.
            plan_parse_failed = (
                "plan_parse_failed" in (planner_out.warnings or ())
            )
            if plan_parse_failed:
                _parse_diag = dict(getattr(planner_out, "diagnostics", {}) or {})
                self.log.log(
                    "plan_parse_failed",
                    {
                        "attempt": attempt,
                        "warnings": list(planner_out.warnings),
                        "raw_chars": len(planner_out.raw_response),
                        # Sanitised, length-capped preview (no full secrets).
                        "raw_preview": _parse_diag.get("raw_preview")
                        or planner_out.raw_response[:240],
                        "diagnostics": _parse_diag,
                    },
                )
                attempt_failures.append(
                    ReplanTrigger(
                        code="plan_parse_failed",
                        step_id="planner",
                        tool_name=None,
                        arguments={},
                        reason=(
                            "Planner LLM reply did not parse as JSON "
                            f"(raw_chars={len(planner_out.raw_response)})."
                        ),
                        attempt=attempt,
                    )
                )

            for step, outcome, trigger in self._execute_steps_parallel(plan.steps):
                if outcome is None:
                    step.status = "failed"
                    if trigger is not None:
                        attempt_failures.append(trigger)
                    continue
                attempt_artifacts[outcome["label"]] = {
                    "tool": outcome["tool"],
                    "output": outcome["output"],
                    "issues": outcome["issues"],
                }
                # MVP-14.1 — typed evidence. The output is already
                # redacted (see _execute_step), so the excerpt that
                # ends up on disk in the chain is safe.
                ev = evidence_from_tool_result(
                    tool_name=outcome["tool"],
                    arguments=outcome.get("arguments"),
                    output=outcome["output"],
                    status="success",
                )
                if ev is not None:
                    attempt_chain.add(ev)
                step.status = "done"
                _cp.save_act(
                    label=outcome["label"],
                    tool=outcome["tool"],
                    chars=len(str(outcome["output"])),
                    status="done",
                )

            # Success: either a 0-step plan (general-knowledge / history-only
            # answer is intentional) or at least one artifact came through.
            # `plan_parse_failed` is NOT success — empty `sources` came from
            # a JSON parse failure, not from the planner choosing zero tools.
            if (not plan.steps and not plan_parse_failed) or attempt_artifacts:
                artifacts = attempt_artifacts
                chain = attempt_chain
                break

            # Failure: this attempt produced nothing usable. Carry the
            # triggers forward and ask the policy what to do next.
            failure_history.extend(attempt_failures)

            # MAST FM-1.5 — stagnation check: same failure signature twice
            # in a row means the loop is looping. Observational only.
            try:
                _stag = self._termination_guard.observe_attempt(
                    attempt=attempt,
                    failure_codes=[t.code for t in attempt_failures],
                    artifact_labels=list(attempt_artifacts.keys()),
                )
                if _stag is not None:
                    self.log.log("stagnation_detected", _stag.to_log_payload())
            except Exception:
                pass

            decision = self.replan_policy.decide(
                failure_history=failure_history,
                completed_attempts=attempt,
            )

            if decision.action == "continue":
                # Log `replan` ONLY when we are going to try again.
                # Pairs neatly with `replan_attempt` (next iteration).
                self.log.log(
                    "replan",
                    {
                        "attempt": attempt,
                        "next_attempt": attempt + 1,
                        "max_total": self.replan_policy.max_total_replans,
                        "triggers": [t.code for t in attempt_failures],
                        "details": [
                            {
                                "step_id": t.step_id,
                                "tool": t.tool_name,
                                "code": t.code,
                                "reason": t.reason,
                            }
                            for t in attempt_failures
                        ],
                        "decision": decision.to_log_payload(),
                    },
                )
                advice_for_planner = decision.advice_for_planner
                forbidden_actions = decision.forbidden_actions
                continue

            # Policy said stop. Emit a structured exhaustion event AND
            # an `error` event (kept for backward-compat with existing
            # consumers that grep on `code=replan_exhausted`). The
            # synthesizer takes over with an honest explanation.
            replan_exhausted = True
            err = ErrorObject(
                source="loop",
                code="replan_exhausted",
                message=(
                    f"Re-planning stopped after {attempt} attempt(s): "
                    f"{decision.reason}. Failure codes: "
                    f"{[t.code for t in failure_history]}"
                ),
                severity="error",
                recoverable=False,
                context={
                    "attempts": attempt,
                    "max_total": self.replan_policy.max_total_replans,
                    "decision_action": decision.action,
                    "decision_reason": decision.reason,
                    "failure_counts": dict(decision.failure_counts),
                    "failure_codes": [t.code for t in failure_history],
                },
            )
            self.log.log("error", err)
            self.log.log(
                "replan_exhausted",
                {
                    "attempts": attempt,
                    "max_total": self.replan_policy.max_total_replans,
                    "decision_action": decision.action,
                    "decision_reason": decision.reason,
                    "failure_counts": dict(decision.failure_counts),
                    "triggers": [t.code for t in attempt_failures],
                },
            )
            break

        # planner_out and plan are guaranteed set here (the for loop ran at
        # least once because max_replan_attempts >= 1 is enforced in __init__).
        assert planner_out is not None and plan is not None

        # MVP-14.1 — fold memory & user-directive evidence into the chain.
        # The tool-level evidence was added per step inside the attempt
        # loop; persistent memory and explicit-consent inputs come from
        # different code paths, so we surface them HERE so the Verifier
        # sees a single uniform chain.

        if persistent_block and self.persistent_store is not None:
            try:
                # `persistent_block` was built from a small set of records
                # in `_retrieve_persistent`; we replay that retrieval cheaply
                # by re-asking the store for the keyword match.
                for rec in self._last_persistent_records:
                    chain.add(
                        evidence_from_memory_record(
                            record_id=rec.id,
                            content=rec.content,
                            source=getattr(rec, "source", None),
                            created_at=getattr(rec, "created_at", None),
                        )
                    )
            except Exception:
                # Defence-in-depth: chain assembly must NEVER abort the
                # run. A malformed record stays out of the chain but the
                # loop completes normally.
                pass

        # MVP-14.1b — fold Working Memory (cached tool outputs from prior
        # turns) into the chain so the Verifier can resolve [memory:…]
        # citations that reference conversation-history artefacts.
        # The LLM may generate citation bodies like `turn_3_test_results`;
        # we expose the artefact label and turn index in source_id so the
        # token-overlap fallback has material to work with.
        if self.memory is not None:
            for _art in self.memory.artifacts.values():
                try:
                    _label = str(_art.get("label", ""))
                    _tidx = int(_art.get("turn_index", 0))
                    _output = _art.get("output")
                    if _output is None or not _label:
                        continue
                    # Sanitise label for use in source_id: replace `:` with
                    # `_` so it doesn't confuse the citation prefix parser.
                    _sid_label = _label.replace(":", "_")
                    chain.add(make_evidence(
                        kind="memory",
                        source_id=f"memory:working_turn_{_tidx}_{_sid_label}",
                        obtained_via="working_memory",
                        claim=f"Cached tool output from turn {_tidx}: {_label}",
                        excerpt=str(_output)[:500],
                        confidence=0.85,
                    ))
                except Exception:
                    pass

        # Store the chain on the agent so tests / future Verifier code
        # can consult it after `run()` returns.
        self.last_provenance = chain

        # MAST FM-3.1 — premature completion risk: empty evidence chain
        # on a question whose phrasing demanded tools. Observational.
        try:
            _pc = self._termination_guard.check_completion(
                question=user_question,
                chain_size=len(chain),
                had_any_artifacts=bool(artifacts),
            )
            if _pc is not None:
                self.log.log(
                    "premature_completion_risk", _pc.to_log_payload()
                )
        except Exception:
            pass

        self.log.log(
            "evidence_collected",
            {
                "count": len(chain),
                "kinds": sorted({ev.kind for ev in chain.evidences}),
                "chain": chain.to_log_payload(),
            },
        )
        source_ranking = rank_chain(chain, question=user_question)
        self.last_source_ranking = source_ranking
        self.log.log("source_ranking", source_ranking.to_log_payload())
        if cheap_path_active:
            # Cheap path: the chain is empty (no tools ran) so the knowledge
            # pipeline and source-registry build have nothing to catalog.
            # Skip them to avoid the per-turn cost the user flagged, and keep
            # the empty registry reset at the top of run().
            source_registry = self.last_source_registry
            self.log.log(
                "knowledge_pipeline_skipped",
                {"reason": "cheap_path", "chain_size": len(chain)},
            )
        else:
            knowledge_result = self.knowledge_pipeline.run(
                chain,
                ranking=source_ranking,
                source_store=self.source_registry_store,
                remember=self._knowledge_remember_batch(),
                auto_write_memory=self.knowledge_auto_write,
            )
            source_registry = knowledge_result.registry
            self.last_source_registry = source_registry
            self.log.log("source_registry", source_registry.to_log_payload())
            self.last_knowledge_pipeline = knowledge_result
            self.log.log("knowledge_pipeline", knowledge_result.to_log_payload())

        # 7. Respond. When replan exhausted the synthesizer still produces
        # a structured Output Contract reply — it gets the failure history
        # and is told to explain honestly what was tried and why nothing
        # worked. This is a much better UX than a bare error string.
        # Layer 5 — expose current-run assumptions to _synthesize via instance.
        self._run_assumptions_current = _run_assumptions
        # Cheap path: force the LIGHT (cheap/fast) synthesizer tier even when
        # the complexity heuristic would return STANDARD (e.g. a config-flag
        # echo carries no LIGHT signal), and trim the prompt to just the
        # essentials — a one-line greeting/flag never needs long-term memory.
        _synth_llm = _task_synth_llm
        if cheap_path_active:
            try:
                from core.task_complexity import ComplexityTier
                _synth_llm = self.model_router.for_task(
                    ModelRole.SYNTHESIZER,
                    user_question,
                    force_tier=ComplexityTier.LIGHT,
                )
                self.log.log(
                    "cheap_path_synth_model",
                    {"model": getattr(_synth_llm, "model", None)},
                )
            except Exception:
                _synth_llm = _task_synth_llm
        draft_answer = self._synthesize(
            goal=goal,
            artifacts=artifacts,
            question=user_question,
            planner_reasoning=planner_out.reasoning,
            history=history,
            persistent_block=persistent_block,
            cycle_findings=list(self._cycle_findings),
            failure_history=failure_history if replan_exhausted else None,
            llm=_synth_llm,
            lean_context=cheap_path_active,
        )

        # 7.5 — MVP-14.4 Verifier. LLM is the DRAFT writer; the Verifier
        # gates what reaches the user. Every claim must be cited (LLM
        # follows the citation grammar in SYSTEM_ANSWER); the Verifier
        # rewrites matched citations to `[verified:<kind>:<src>]` and
        # tags uncited claims with `[unverified]`. A fully-uncited
        # answer earns an explicit disclaimer so the user can never
        # mistake an unsourced answer for a verified one.
        #
        # MVP-14.5 — when the LLM cites [web:URL] but no web_page evidence
        # exists for that URL (Verifier verdict `cited_but_unmatched`),
        # we treat this as a structured failure (`unresolved_citation`)
        # and feed it back through the SAME ReplanPolicy that already
        # governs tool-level failures. The next planner call is told
        # exactly which URLs to fetch; once web_fetch runs, the original
        # draft is re-verified on the enriched chain — no second LLM
        # synthesis is needed because the draft already cites the URLs.
        if self.verifier_enabled:
            from core.verifier import (
                extract_unresolved_web_urls,
                verify as _verify,
            )

            report = _verify(answer=draft_answer, chain=chain, user_question=user_question)
            self.log.log("verification", report.to_log_payload())

            # P1 — observational cross-subsystem audit. Compare planner
            # outcome (steps done/failed, artifacts produced) against
            # verifier verdict and emit a `subsystem_disagreement` event
            # for each conflicting pair. Logging only — no behaviour
            # change at this layer.
            _disagreements: list[dict] = []
            try:
                from core.subsystem_disagreement import detect_disagreements
                _disagreements = detect_disagreements(
                    attempt=attempt,
                    plan_steps=plan.steps,
                    artifacts=artifacts,
                    report=report,
                    failure_history=failure_history,
                )
                for _ev in _disagreements:
                    self.log.log("subsystem_disagreement", _ev)
            except Exception:
                pass

            # P1/P2 — confidence vector. Decompose the scalar gate into
            # three axes (evidence / coherence / relevance) so triage
            # can target the right subsystem when something is off.
            # Logging only.
            try:
                from core.confidence_vector import compute_vector
                _cv = compute_vector(
                    report=report,
                    disagreements=_disagreements,
                    question=user_question,
                    answer=draft_answer,
                )
                self.log.log("confidence_vector", _cv.to_log_payload())
            except Exception:
                pass
            if report.malformed_output:
                self.log.log(
                    "output_contract_violation",
                    {
                        "reason": "LLM answer contains no Output Contract section headers",
                        "total_chunks": report.total_chunks,
                        "structural_chunks": report.structural_chunks,
                    },
                )
            self.last_verification = report
            self.last_provenance = chain

            # Berkeley MAST 2025 / Horvitz 1999 — post-verifier confidence
            # gate. Observational only: the loop may still ship the answer,
            # but a `low_confidence_gate` event is emitted when the
            # verified/cited mass is below threshold so operators can
            # detect over-confident-but-unsupported answers.
            try:
                _gate = self._confidence_gate.evaluate(report)
                if _gate.triggered:
                    self.log.log(
                        "low_confidence_gate", _gate.to_log_payload()
                    )
            except Exception:
                pass

            verify_replan_attempt = 0
            VERIFY_REPLAN_HARD_CAP = 2  # belt + braces over ReplanPolicy

            while True:
                unresolved_urls = extract_unresolved_web_urls(report)
                if not unresolved_urls:
                    break
                if verify_replan_attempt >= VERIFY_REPLAN_HARD_CAP:
                    self.log.log(
                        "verify_replan_capped",
                        {
                            "attempts": verify_replan_attempt,
                            "hard_cap": VERIFY_REPLAN_HARD_CAP,
                            "unresolved_count": len(unresolved_urls),
                            "unresolved_sample": unresolved_urls[:3],
                        },
                    )
                    break

                verify_replan_attempt += 1
                trigger = ReplanTrigger(
                    code="unresolved_citation",
                    step_id=f"verify-{verify_replan_attempt}",
                    tool_name=None,
                    arguments={"urls": list(unresolved_urls)},
                    reason=(
                        f"Verifier found {len(unresolved_urls)} [web:...] "
                        f"citation(s) the chain cannot resolve. "
                        f"Planner must add web_fetch for: {unresolved_urls}"
                    ),
                    attempt=attempt + verify_replan_attempt,
                )
                failure_history.append(trigger)

                decision = self.replan_policy.decide(
                    failure_history=failure_history,
                    completed_attempts=attempt + verify_replan_attempt,
                )

                if decision.action != "continue":
                    replan_exhausted = True
                    self.log.log(
                        "replan_exhausted",
                        {
                            "phase": "verify",
                            "attempts": attempt + verify_replan_attempt,
                            "max_total": self.replan_policy.max_total_replans,
                            "decision_action": decision.action,
                            "decision_reason": decision.reason,
                            "failure_counts": dict(decision.failure_counts),
                            "triggers": ["unresolved_citation"],
                        },
                    )
                    break

                # Build the planner advice. We surface `unresolved_citation`
                # advice FIRST (the new failure dominates) and then append
                # an explicit URL list the planner must convert into
                # web_fetch steps. The base `decision.advice_for_planner`
                # is composed over all FailureTypes seen so far, in
                # FailureType-declaration order, so it might bury the
                # critical fetch instruction — we prepend ours explicitly.
                base_advice = (
                    self.replan_policy.budgets["unresolved_citation"].advice
                )
                urls_block = "\n".join(f"  - {u}" for u in unresolved_urls)
                verify_advice = (
                    f"{base_advice}\n\n"
                    f"URLs that MUST be opened via web_fetch (one step each):"
                    f"\n{urls_block}"
                )

                self.log.log(
                    "replan",
                    {
                        "phase": "verify",
                        "attempt": attempt + verify_replan_attempt - 1,
                        "next_attempt": attempt + verify_replan_attempt,
                        "max_total": self.replan_policy.max_total_replans,
                        "triggers": ["unresolved_citation"],
                        "details": [
                            {
                                "step_id": trigger.step_id,
                                "tool": None,
                                "code": "unresolved_citation",
                                "reason": trigger.reason,
                            },
                        ],
                        "decision": decision.to_log_payload(),
                        "unresolved_urls": list(unresolved_urls),
                    },
                )

                # Bump the public attempt counter so `respond.attempts_used`
                # reflects the verify-driven re-plans honestly.
                self._current_attempt = attempt + verify_replan_attempt

                self.log.log(
                    "replan_attempt",
                    {
                        "phase": "verify",
                        "attempt": attempt + verify_replan_attempt,
                        "max_total": self.replan_policy.max_total_replans,
                        "advice_chars": len(verify_advice),
                        "forbidden_action_count": len(decision.forbidden_actions),
                        "failure_counts_so_far": dict(
                            self._count_failures(failure_history)
                        ),
                    },
                )

                failure_context = self._format_replan_context(
                    failure_history,
                    attempt + verify_replan_attempt,
                    self.replan_policy.max_total_replans,
                    advice=verify_advice,
                    forbidden_actions=decision.forbidden_actions,
                )

                planner_out = self.planner.plan(
                    question=user_question,
                    file_hint=file_hint,
                    history=planner_history,
                    failure_context=failure_context,
                    forbidden_actions=decision.forbidden_actions,
                )
                planner_out = self._force_file_hint_read_when_explicit(
                    planner_out,
                    question=user_question,
                    file_hint=file_hint,
                )
                self.log.log(
                    "planner",
                    {
                        "phase": "verify",
                        "reasoning": planner_out.reasoning,
                        "tools_chosen": [s["tool"] for s in planner_out.sources],
                        "warnings": planner_out.warnings,
                        "raw_chars": len(planner_out.raw_response),
                        "attempt": attempt + verify_replan_attempt,
                        "replan_context_chars": len(failure_context),
                    },
                )

                verify_plan = self._build_plan(goal, planner_out.sources)
                self.log.log(
                    "plan",
                    verify_plan,
                    steps=len(verify_plan.steps),
                    attempt=attempt + verify_replan_attempt,
                    phase="verify",
                )

                # Execute the new steps. We tolerate sanitiser/policy drops
                # and per-step failures — if NOTHING gets fetched we'll
                # just exit on the next loop iteration when re-verify
                # still finds the same unresolved URLs. No infinite loop
                # because the hard cap + per-type budget both bound us.
                added_evidence = 0
                for step, outcome, trigger in self._execute_steps_parallel(verify_plan.steps):
                    if outcome is None:
                        step.status = "failed"
                        # Drain the scratch trigger so it doesn't leak into
                        # the next decide() iteration with a misleading code
                        # (e.g. tool_error for a fetch that was sanitised).
                        if trigger is not None:
                            failure_history.append(trigger)
                        continue
                    artifacts[outcome["label"]] = {
                        "tool": outcome["tool"],
                        "output": outcome["output"],
                        "issues": outcome["issues"],
                    }
                    ev = evidence_from_tool_result(
                        tool_name=outcome["tool"],
                        arguments=outcome.get("arguments"),
                        output=outcome["output"],
                        status="success",
                    )
                    if ev is not None:
                        chain.add(ev)
                        added_evidence += 1
                    step.status = "done"

                # Re-verify the ORIGINAL draft against the enriched chain.
                # The draft already cites these URLs (that's why they
                # were unresolved); now the chain has the web_page
                # evidence so `match_citation` will resolve them.
                report = _verify(answer=draft_answer, chain=chain, user_question=user_question)
                self.log.log(
                    "verification",
                    {
                        **report.to_log_payload(),
                        "phase": "verify",
                        "iteration": verify_replan_attempt,
                        "evidence_added": added_evidence,
                    },
                )
                self.last_verification = report
                self.last_provenance = chain

                # Re-emit the chain snapshot so callers / log consumers
                # see the enriched provenance after each fetch round.
                self.log.log(
                    "evidence_collected",
                    {
                        "phase": "verify",
                        "count": len(chain),
                        "kinds": sorted({ev.kind for ev in chain.evidences}),
                        "chain": chain.to_log_payload(),
                    },
                )
                source_ranking = rank_chain(chain, question=user_question)
                self.last_source_ranking = source_ranking
                self.log.log(
                    "source_ranking",
                    {
                        **source_ranking.to_log_payload(),
                        "phase": "verify",
                        "iteration": verify_replan_attempt,
                    },
                )
                knowledge_result = self.knowledge_pipeline.run(
                    chain,
                    ranking=source_ranking,
                    source_store=self.source_registry_store,
                    remember=self._knowledge_remember_batch(),
                    auto_write_memory=self.knowledge_auto_write,
                )
                source_registry = knowledge_result.registry
                self.last_source_registry = source_registry
                self.log.log(
                    "source_registry",
                    {
                        **source_registry.to_log_payload(),
                        "phase": "verify",
                        "iteration": verify_replan_attempt,
                    },
                )
                self.last_knowledge_pipeline = knowledge_result
                self.log.log(
                    "knowledge_pipeline",
                    {
                        **knowledge_result.to_log_payload(),
                        "phase": "verify",
                        "iteration": verify_replan_attempt,
                    },
                )

                # If the fetch round added zero evidence, re-verify will
                # produce the same unresolved list — exit instead of
                # looping back to a planner call that has nothing new
                # to work with.
                if added_evidence == 0:
                    self.log.log(
                        "verify_replan_noop",
                        {
                            "iteration": verify_replan_attempt,
                            "unresolved_count_before": len(unresolved_urls),
                            "unresolved_count_after": len(
                                extract_unresolved_web_urls(report)
                            ),
                        },
                    )
                    break

            answer = report.annotated_answer
        else:
            answer = draft_answer
            self.last_verification = None

        policy_result = apply_ranker_output_policy(
            answer=answer,
            ranking=self.last_source_ranking,
            question=user_question,
            replan_exhausted=replan_exhausted,
        )
        if policy_result.applied:
            answer = policy_result.answer
            self.log.log("output_policy", policy_result.to_log_payload())

        # B-1 Clarification Gate — режим переспроса. When the loop is STUCK
        # (replan exhausted == loop_suspected), the mature response is to ASK,
        # not to keep building. Prepend the gate's minimal clarifying questions
        # to the honest answer so the operator can narrow the frame. Pure and
        # deterministic (no LLM, no I/O); best-effort so it can never take down
        # the response path.
        if replan_exhausted and self.clarification_gate_enabled:
            try:
                from core.clarification_gate import for_loop_suspected
                _clarify = for_loop_suspected()
                _clarify_prompt = _clarify.prompt()
                if _clarify_prompt and _clarify_prompt not in answer:
                    self.log.log("clarification_gate", _clarify.to_dict())
                    answer = f"{_clarify_prompt}\n\n{answer}"
            except Exception:
                pass

        # Low-evidence enforcement: if the verifier's verdict
        # distribution is severely below threshold (few verified, many
        # unverified, long enough draft), replace the long polished
        # answer with a deterministic short reply that lists ONLY the
        # verified claims and explicitly states "insufficient data".
        # The informational ConfidenceGate already logs the warning;
        # this is the structural enforcement paired with it.
        if self.last_verification is not None:
            try:
                _le = evaluate_low_evidence_policy(
                    answer=answer,
                    report=self.last_verification,
                    question=user_question,
                )
                if _le.triggered:
                    self.log.log(
                        "low_evidence_truncation", _le.to_log_payload()
                    )
                    answer = _le.answer
            except Exception:
                # Defence-in-depth: truncation must NEVER take down
                # the loop. A failed evaluation falls back to the
                # original answer the user would otherwise have got.
                pass

        # Strip internal verification markers before user-facing output.
        # Must happen AFTER output_policy which needs [verified:...] markers.
        answer = _strip_verification_markers(answer)

        file_scope_notice = self._file_scope_notice(user_question, artifacts)
        if file_scope_notice and file_scope_notice not in answer:
            answer = f"{file_scope_notice}\n\n{answer}"
            self.log.log(
                "file_scope_notice",
                {
                    "notice": file_scope_notice,
                    "artifact_labels": list(artifacts.keys()),
                },
            )

        # Defence-in-depth: redact once more on the way out so even an
        # LLM hallucinating a credential or PII cannot bypass the kernel.
        safe_answer, answer_findings, answer_pii_findings = redact_dlp_text(answer)
        if answer_findings:
            self.log.log(
                "secret_detected",
                {
                    "label": "final_answer",
                    "kinds": sorted({f.kind for f in answer_findings}),
                    "count": len(answer_findings),
                    "surface": "user_output",
                },
            )
        if answer_pii_findings:
            pii_kinds = sorted({f"pii-{f.kind}" for f in answer_pii_findings})
            self.log.log(
                "sensitive_detected",
                {
                    "label": "final_answer",
                    "kinds": pii_kinds,
                    "count": len(answer_pii_findings),
                    "surface": "user_output",
                },
            )
        answer = safe_answer

        self.log.log(
            "respond",
            {
                "chars": len(answer),
                "sources": list(artifacts.keys()) or ["general-knowledge"],
                "redactions": len(self._cycle_findings or []),
                "attempts_used": self._current_attempt,
                "replan_exhausted": replan_exhausted,
            },
        )
        _cp.save_respond(answer=answer)

        # Memory write — append the completed turn
        if self.memory is not None:
            turn = self.memory.record_turn(
                question=user_question,
                planner_reasoning=planner_out.reasoning,
                tools_used=[s["tool"] for s in planner_out.sources],
                artifact_labels=list(artifacts.keys()),
                answer=answer,
            )
            self.log.log(
                "memory_write",
                {
                    "session_id": self.memory.session_id,
                    "turn_id": turn.id,
                    "turn_index": turn.index,
                    "tools_used": turn.tools_used,
                    "labels": turn.artifact_labels,
                    },
                )
            # Anthropic 2025 context engineering — compact older turns into
            # one summary Turn instead of silently dropping them when
            # max_turns is exceeded. No-op until the threshold is crossed.
            try:
                if self.memory.compact_if_needed():
                    self.log.log(
                        "memory_compacted",
                        {
                            "session_id": self.memory.session_id,
                            "turns_after": len(self.memory.turns),
                        },
                    )
            except Exception:
                pass

        verification = self.last_verification
        self._record_experience_memory(
            goal_description=goal.description,
            question=user_question,
            answer=answer,
            tools_used=[s["tool"] for s in planner_out.sources],
            source_labels=list(artifacts.keys()) or ["general-knowledge"],
            verified_chunks=verification.verified_chunks if verification else 0,
            unverified_chunks=verification.unverified_chunks if verification else 0,
            replan_exhausted=replan_exhausted,
            skip_consolidation=cheap_path_active,
        )

        # Layer 4 — update user profile from this interaction.
        if self.user_profile_store is not None:
            try:
                updated = self.user_profile_store.update_from_interaction(
                    question=user_question,
                    response=answer,
                    base=self.last_user_profile,
                )
                self.last_user_profile = updated
                self.log.log(
                    "user_profile_update",
                    {
                        "expertise": updated.expertise,
                        "verbosity": updated.verbosity,
                        "language": updated.language,
                        "interaction_count": updated.interaction_count,
                        "interests": updated.interests,
                        "expert_signals": updated.expert_signals,
                        "novice_signals": updated.novice_signals,
                    },
                )
            except Exception:
                pass  # Profile update must never abort the run.

        # Layer 5 — persist assumptions and expose via last_assumptions.
        self.last_assumptions = _run_assumptions
        if self.assumption_store is not None and _run_assumptions.new_assumptions:
            try:
                self.assumption_store.save_many(_run_assumptions.new_assumptions)
            except Exception:
                pass  # Store failure must never abort the run.

        # Clear streaming callback so it cannot leak into the next turn.
        self._stream_on_token = None
        return answer

    # ---------- persistent memory facade ----------

    def _remember_from_knowledge(
        self,
        content: str,
        tags: list[str],
        source: str,
        record_type: str,
        owner: str,
    ) -> tuple[MemoryWriteDecision, MemoryRecord | None]:
        return self.remember(
            content=content,
            tags=tags,
            source=source,
            record_type=record_type,
            owner=owner,
        )

    def _knowledge_remember_batch(self) -> RememberFn:
        """Return a ``remember`` callback that loads the persistent store ONCE.

        The knowledge pipeline attempts a write for every extracted claim. On a
        repeated read-only turn (e.g. a work-session re-running the same goal)
        the same ~60 claims are re-extracted from the same files each cycle, and
        each `remember()` used to reload the entire store to run the dedup gate —
        an O(claims × records) disk reload every cycle whose writes are ~all
        rejected as duplicates (TD-019). This closure loads the snapshot a single
        time per pipeline pass and reuses it; ``remember`` keeps it current by
        appending any saved record, so dedup/echo behavior is unchanged.
        """
        snapshot: list[MemoryRecord] = (
            self.persistent_store.load() if self.persistent_store is not None else []
        )

        def _remember(
            content: str,
            tags: list[str],
            source: str,
            record_type: str,
            owner: str,
        ) -> tuple[MemoryWriteDecision, MemoryRecord | None]:
            return self.remember(
                content=content,
                tags=tags,
                source=source,
                record_type=record_type,
                owner=owner,
                existing=snapshot,
            )

        return _remember

    def remember(
        self,
        content: str,
        tags: list[str] | None = None,
        source: str = "user-explicit",
        record_type: str = "semantic",
        owner: str = "user",
        existing: list[MemoryRecord] | None = None,
    ) -> tuple[MemoryWriteDecision, MemoryRecord | None]:
        """Run a `:remember`-style write through the Write Policy.

        Returns the decision plus the saved record (or None on reject).
        The store must be wired; otherwise a reject decision is returned.

        `owner` flows into the Write Policy. Anything outside the first-
        party whitelist needs a `cross-owner-consent` tag.

        ``existing`` is an optional pre-loaded snapshot of the persistent
        records used by the dedup gate. When a caller writes many records in
        one pass (e.g. the knowledge pipeline attempting dozens of agent-auto
        claims per cycle) it can load the store ONCE and pass the same list in,
        avoiding a full store reload per write. When provided, any record saved
        here is appended to it so later writes in the same pass still dedup
        against it — matching the reload-every-time semantics exactly. When
        ``None`` (the default) the store is loaded fresh, as before.
        """
        if self.persistent_store is None:
            decision = MemoryWriteDecision(
                "reject", ["persistent store not configured"]
            )
            self.log.log("persistent_memory_write", {"decision": decision.decision, "reasons": decision.reasons})
            return decision, None

        tags = tags or []
        # Dedup gate (MVP-10) needs the existing records. Load them once and
        # pass into the policy so the policy stays a pure function. A caller
        # may supply the snapshot (TD-019) to avoid reloading per write.
        existing = self.persistent_store.load() if existing is None else existing
        # Echo gate (A1) needs the recent agent-auto write-log, time-windowed.
        # Only consulted when a registry is wired; stays a pure input to the
        # policy. `user-explicit` writes are never echo-guarded.
        recent_writes: list[MemoryWriteEvent] = []
        if self.memory_write_registry is not None:
            try:
                recent_writes = self.memory_write_registry.recent()
            except Exception:
                recent_writes = []  # Registry hiccup must never block a write.
        decision = self.write_policy.decide(
            content=content,
            tags=tags,
            source=source,
            owner=owner,
            existing=existing,
            recent_writes=recent_writes,
        )
        if decision.decision == "reject":
            self.log.log(
                "persistent_memory_write",
                {
                    "decision": "reject",
                    "reasons": decision.reasons,
                    "policy_id": decision.policy_id,
                    "source": source,
                    "tag_count": len(tags),
                    "content_chars": len(content or ""),
                },
            )
            return decision, None

        safe_content, _secret_findings, pii_findings = redact_dlp_text(content.strip())
        if pii_findings:
            self.log.log(
                "sensitive_detected",
                {
                    "label": "persistent_memory_candidate",
                    "kinds": sorted({f"pii-{f.kind}" for f in pii_findings}),
                    "count": len(pii_findings),
                    "surface": "persistent_memory",
                },
            )

        record = MemoryRecord(
            type=record_type,  # type: ignore[arg-type]
            content=safe_content.strip(),
            tags=tags,
            owner=owner,
        )
        self.persistent_store.save(record)
        # Keep a caller-supplied snapshot (TD-019) current so subsequent writes
        # in the same pass dedup against this record, exactly as a fresh reload
        # would have. Harmless when ``existing`` is a private freshly-loaded list.
        existing.append(record)
        # Record this agent-auto write in the rolling echo-log so the next
        # cycle's echo gate can see it. Only agent-auto writes are logged —
        # the registry exists to catch the agent echoing *itself*.
        if (
            self.memory_write_registry is not None
            and (source or "").strip().lower() == "agent-auto"
        ):
            try:
                self.memory_write_registry.append(
                    make_event(
                        record.content if isinstance(record.content, str) else str(record.content),
                        tags=tags,
                        record_type=record_type,
                        source=source,
                        cycle_id=getattr(self.log, "trace_id", "") or "",
                    )
                )
            except Exception:
                pass  # Echo-log write must never abort the memory write.
        self.log.log(
            "persistent_memory_write",
            {
                "decision": "save",
                "reasons": decision.reasons,
                "policy_id": decision.policy_id,
                "record_id": record.id,
                "tags": record.tags,
                "type": record.type,
                "chars": len(record.content) if isinstance(record.content, str) else 0,
            },
        )
        return decision, record

    def forget(self, record_id: str | None = None) -> int:
        """Delete one record (by id) or all records. Returns deletion count."""
        if self.persistent_store is None:
            return 0
        if record_id is None:
            n = self.persistent_store.delete_all()
            self.log.log("persistent_memory_delete", {"scope": "all", "deleted": n})
            return n
        ok = self.persistent_store.delete(record_id)
        self.log.log(
            "persistent_memory_delete",
            {"scope": "one", "record_id": record_id, "deleted": int(ok)},
        )
        return 1 if ok else 0

    def list_persistent(self) -> list[MemoryRecord]:
        if self.persistent_store is None:
            return []
        return self.persistent_store.load()

    # ------------------------------------------------------------------
    # MVP-11 Compensation surface (rollback / undo)
    # ------------------------------------------------------------------

    def propose_repair(
        self,
        *,
        target_path: str,
        workspace_root: "Path",
        test_paths: tuple[str, ...] = ("tests",),
        test_pattern: str | None = None,
        trace_id: str | None = None,
        extra_context: str = "",
    ):
        """Generate a guarded RepairProposal without applying it."""
        from core.repair_proposal import RepairProposalGenerator

        return RepairProposalGenerator(
            workspace_root=workspace_root,
            llm=self.model_router.for_role(ModelRole.REPAIR_PROPOSAL),
            logger=self.log,
        ).generate(
            target_path=target_path,
            test_paths=test_paths,
            test_pattern=test_pattern,
            trace_id=trace_id,
            extra_context=extra_context,
        )

    def repair(
        self,
        proposal: "Any",
        *,
        workspace_root: "Path",
    ):
        """Run one self-repair proposal through the MVP-13.2 controller."""
        from core.self_repair import SelfRepairController

        return SelfRepairController(
            self,
            workspace_root=workspace_root,
        ).run(proposal)

    def rollback(
        self,
        plan_id: str | None = None,
        *,
        workspace_root: "Path | None" = None,
    ):
        """Apply the most recent compensation plan (or one by id).

        Returns the `CompensationReport`. When the log is empty, the
        report carries zero outcomes and the audit event records the
        no-op. When `plan_id` is provided, the matching plan is
        removed from the log; otherwise the LAST plan is popped.

        `workspace_root` is required because `AgentLoop` is workspace-
        agnostic by construction. The CLI passes it from the main()
        --workspace argument; tests pass the same root the producing
        tool used.
        """
        from core.compensation import CompensationReport, apply_compensation_plan

        if workspace_root is None:
            report = CompensationReport(plan_id=plan_id or "", workspace_root="")
            self.log.log(
                "compensation_apply",
                {**report.summary(), "skipped_reason": "no workspace_root supplied"},
            )
            return report

        if not self.compensation_log:
            report = CompensationReport(
                plan_id=plan_id or "", workspace_root=str(Path(workspace_root).resolve())
            )
            self.log.log(
                "compensation_apply",
                {**report.summary(), "skipped_reason": "no plans registered"},
            )
            return report

        if plan_id is None:
            plan = self.compensation_log.pop()
        else:
            for i, p in enumerate(self.compensation_log):
                if p.id == plan_id:
                    plan = self.compensation_log.pop(i)
                    break
            else:
                report = CompensationReport(
                    plan_id=plan_id, workspace_root=str(Path(workspace_root).resolve())
                )
                self.log.log(
                    "compensation_apply",
                    {**report.summary(), "skipped_reason": f"plan_id '{plan_id}' not found"},
                )
                return report

        report = apply_compensation_plan(plan, Path(workspace_root))
        self.log.log("compensation_apply", report.summary())
        return report

    # ------------------------------------------------------------------
    # MVP-10 Memory Hygiene surface
    # ------------------------------------------------------------------
    # Every hygiene operation is a discrete, deliberate call. The CLI
    # exposes them via `:hygiene <subcmd>`. Each method logs ONE event
    # carrying the typed report's `summary()` so audits show exactly
    # what was removed and why.

    def cleanup_backups(
        self,
        workspace_root,
        *,
        keep_last: int | None = None,
        max_age_days: int | None = None,
        dry_run: bool = False,
    ):
        """Delete old `.bak.<ts>` files under the workspace root."""
        from core.hygiene import (
            DEFAULT_KEEP_LAST,
            DEFAULT_MAX_AGE_DAYS,
            cleanup_backups,
        )

        report = cleanup_backups(
            workspace_root,
            keep_last=DEFAULT_KEEP_LAST if keep_last is None else keep_last,
            max_age_days=DEFAULT_MAX_AGE_DAYS if max_age_days is None else max_age_days,
            dry_run=dry_run,
        )
        self.log.log("backup_cleanup", report.summary())
        return report

    def expire_persistent(self, *, dry_run: bool = False):
        """Remove persistent records whose TTL has elapsed."""
        from core.hygiene import expire_memory

        if self.persistent_store is None:
            from core.hygiene import ExpiryReport

            empty = ExpiryReport(dry_run=dry_run)
            self.log.log("persistent_memory_expire", empty.summary())
            return empty
        report = expire_memory(self.persistent_store, dry_run=dry_run)
        self.log.log("persistent_memory_expire", report.summary())
        return report

    def dedupe_persistent(self, *, threshold: float | None = None, dry_run: bool = False):
        """Collapse near-duplicate persistent records (oldest kept)."""
        from core.hygiene import DEFAULT_DEDUP_THRESHOLD, deduplicate_memory

        if self.persistent_store is None:
            from core.hygiene import DedupReport

            empty = DedupReport(
                threshold=DEFAULT_DEDUP_THRESHOLD if threshold is None else threshold,
                dry_run=dry_run,
            )
            self.log.log("persistent_memory_dedupe", empty.summary())
            return empty
        report = deduplicate_memory(
            self.persistent_store,
            threshold=DEFAULT_DEDUP_THRESHOLD if threshold is None else threshold,
            dry_run=dry_run,
        )
        self.log.log("persistent_memory_dedupe", report.summary())
        return report

    def summarise_persistent(
        self,
        tag: str,
        *,
        max_records: int | None = None,
        dry_run: bool = False,
    ):
        """Merge records sharing `tag` into a single summary via the LLM."""
        from core.hygiene import DEFAULT_SUMMARY_MAX_RECORDS, summarise_memory

        if self.persistent_store is None:
            from core.hygiene import SummaryReport

            empty = SummaryReport(tag=tag, skipped_reason="no store", dry_run=dry_run)
            self.log.log("persistent_memory_summarise", empty.summary())
            return empty
        report = summarise_memory(
            self.persistent_store,
            self.model_router.for_role(ModelRole.MEMORY_SUMMARY),
            tag=tag,
            max_records=DEFAULT_SUMMARY_MAX_RECORDS if max_records is None else max_records,
            dry_run=dry_run,
        )
        self.log.log("persistent_memory_summarise", report.summary())
        return report

    def archive_persistent(
        self,
        *,
        threshold: float | None = None,
        min_age_days: int | None = None,
        dry_run: bool = False,
    ):
        """Move low-importance, old records to the archive (never deleted)."""
        from core.hygiene import (
            DEFAULT_ARCHIVE_MIN_AGE_DAYS,
            DEFAULT_ARCHIVE_THRESHOLD,
            ArchiveReport,
            archive_low_value_memory,
        )

        if self.persistent_store is None:
            empty = ArchiveReport(
                threshold=DEFAULT_ARCHIVE_THRESHOLD if threshold is None else threshold,
                min_age_days=DEFAULT_ARCHIVE_MIN_AGE_DAYS if min_age_days is None else min_age_days,
                dry_run=dry_run,
            )
            self.log.log("persistent_memory_archive", empty.summary())
            return empty
        report = archive_low_value_memory(
            self.persistent_store,
            threshold=DEFAULT_ARCHIVE_THRESHOLD if threshold is None else threshold,
            min_age_days=DEFAULT_ARCHIVE_MIN_AGE_DAYS if min_age_days is None else min_age_days,
            dry_run=dry_run,
        )
        self.log.log("persistent_memory_archive", report.summary())
        return report

    def _episodic_store_mtime(self) -> float:
        """Return the modification time of the episodic store file, or 0.0."""
        if self.episodic_store is None:
            return 0.0
        try:
            return self.episodic_store.path.stat().st_mtime
        except OSError:
            return 0.0

    def _retrieve_persistent(self, question: str) -> str:
        """Pick + format relevant persistent records for prompt injection.

        Returns a `<long_term_memory>` XML block, or empty string when no
        store is wired, no records exist, or none score above threshold.
        """
        # MVP-14.1: stash the selected records so the evidence chain
        # can include them after run() finishes. Reset every cycle.
        self._last_persistent_records = []
        if self.persistent_store is None:
            return ""
        records = self.persistent_store.load()
        if not records:
            return ""
        use_report = self.knowledge_use_policy.filter(
            records,
            role_context=self.last_role_context,
            question=question,
        )
        self.log.log("knowledge_use_policy", use_report.to_log_payload())
        if not use_report.allowed:
            self.log.log(
                "persistent_memory_inject",
                {
                    "records_total": len(records),
                    "records_selected": 0,
                    "reason": "no records applicable to current role",
                    "role": self.last_role_context.role,
                },
            )
            return ""
        selected = self.retrieval_policy.select(use_report.allowed, question)
        if not selected:
            self.log.log(
                "persistent_memory_inject",
                {
                    "records_total": len(records),
                    "records_selected": 0,
                    "reason": "no keyword overlap above threshold",
                    "role": self.last_role_context.role,
                    "records_applicable": len(use_report.allowed),
                },
            )
            return ""
        formatted = self.retrieval_policy.format_for_prompt(selected)
        self.log.log(
            "persistent_memory_inject",
            {
                "records_total": len(records),
                "records_selected": len(selected),
                "records_applicable": len(use_report.allowed),
                "ids": [r.id for r in selected],
                "chars": len(formatted),
                "role": self.last_role_context.role,
            },
        )
        self._last_persistent_records = list(selected)

        # Bump access stats on every retrieved record so the archive scorer
        # knows which records are actively useful (≠ junk that was saved but
        # never used again).
        if self.persistent_store is not None and selected:
            from datetime import datetime, timezone as _tz
            _now_dt = datetime.now(_tz.utc)
            for rec in selected:
                updated = rec.model_copy(update={
                    "access_count": rec.access_count + 1,
                    "last_accessed_at": _now_dt,
                })
                self.persistent_store.update(updated)

        return f"<long_term_memory>\n{formatted}\n</long_term_memory>"

    def _retrieve_experience_memory(self, question: str) -> str:
        """Inject compact episodic/procedural memory into planning.

        This is deliberately separate from `<long_term_memory>`:
        persistent memory stores user-approved facts, while experience memory
        stores operational history and reusable workflows. It can guide the
        planner without becoming a source of factual claims in the final answer.

        Re-ask detection: if the current question is very similar (Jaccard ≥ 0.4)
        to the stored *question* field of a past episode, the user is likely asking
        AGAIN because the previous answer was insufficient.  A
        ``<repeat_question_hint>`` block is appended to signal this to the planner.
        """
        self._last_episode_records = []
        self._last_procedure_records = []
        self._last_best_similar_episode = None
        self._last_best_similar_score = 0.0
        if self.episodic_store is None and self.procedural_store is None:
            return ""
        episodes = (
            self.episodic_store.search(question, limit=3)
            if self.episodic_store is not None
            else []
        )
        # ── Surface repair lessons for files mentioned in the question ────
        # search() gives a +50 boost to protected-tag episodes so they usually
        # appear in the top-3, but when the question contains a file path that
        # exactly matches a lesson's summary we fetch them explicitly as a
        # fallback — e.g. ":repair core/foo.py" should always see lessons
        # about core/foo.py even if the token overlap is otherwise low.
        if self.episodic_store is not None:
            lessons = self.episodic_store.search_by_tags(["lesson"], limit=5)
            q_lower = question.lower()
            # Extract path-like tokens: words containing "/" or ending in ".py"
            path_tokens = [
                w.strip("\"',:;()")
                for w in q_lower.split()
                if "/" in w or w.endswith(".py")
            ]
            for lesson in lessons:
                if lesson not in episodes and path_tokens and any(
                    tok in lesson.summary.lower() for tok in path_tokens
                ):
                    episodes.append(lesson)
        procedures = (
            self.procedural_store.search(question, limit=3)
            if self.procedural_store is not None
            else []
        )
        block = format_experience_context(episodes=episodes, procedures=procedures)
        self.log.log(
            "experience_memory_inject",
            {
                "episodes_selected": len(episodes),
                "procedures_selected": len(procedures),
                "episode_ids": [ep.id for ep in episodes],
                "procedure_ids": [proc.id for proc in procedures],
                "chars": len(block),
            },
        )
        self._last_episode_records = list(episodes)
        self._last_procedure_records = list(procedures)

        # ── Re-ask detection ──────────────────────────────────────────────
        # Jaccard similarity on the question tokens only (not goal/summary).
        # High overlap (≥ 0.40) means the user is asking the SAME question
        # again — a strong signal that the previous answer was insufficient.
        _REPEAT_THRESHOLD = 0.40
        if self.episodic_store is not None:
            try:
                repeat_ep, repeat_score = self.episodic_store.find_most_similar(
                    question, threshold=_REPEAT_THRESHOLD
                )
                # Store for fast-path and planner-cache checks in run().
                self._last_best_similar_episode = repeat_ep
                self._last_best_similar_score = repeat_score
                if repeat_ep is not None:
                    quality_pct = int(repeat_ep.answer_quality_score * 100)
                    quality_note = (
                        f"previous answer quality: {quality_pct}% verified — "
                        + ("HIGH" if quality_pct >= 70 else ("MEDIUM" if quality_pct >= 40 else "LOW"))
                    )
                    if quality_pct >= 70:
                        conclusion = (
                            "The previous answer was HIGH quality. "
                            "The user may be re-testing, want more detail, or verifying consistency. "
                            "You MAY confirm the previous answer if nothing has changed, "
                            "but try to add depth or perspective not present before."
                        )
                    else:
                        conclusion = (
                            "The previous answer was likely INSUFFICIENT or INCOMPLETE. "
                            "Do NOT repeat the same approach."
                        )
                    hint = (
                        "<repeat_question_hint>\n"
                        "WARNING: The user is asking a question that is very similar to a "
                        "previously answered one (Jaccard similarity "
                        f"{repeat_score:.2f} ≥ {_REPEAT_THRESHOLD}).\n"
                        f"Past episode: {repeat_ep.id} | outcome={repeat_ep.outcome}\n"
                        f"Past answer quality: {quality_note}\n"
                        f"Previous question: {repeat_ep.question[:200]}\n"
                        f"Previous answer summary: {repeat_ep.summary[:300]}\n"
                        f"CONCLUSION: {conclusion}\n"
                        "Action: try a different strategy, use additional tools, go "
                        "deeper, or explicitly acknowledge what was missing before.\n"
                        "</repeat_question_hint>"
                    )
                    self.log.log(
                        "repeat_question_detected",
                        {
                            "episode_id": repeat_ep.id,
                            "similarity": repeat_score,
                            "threshold": _REPEAT_THRESHOLD,
                            "past_outcome": repeat_ep.outcome,
                            "past_answer_quality": repeat_ep.answer_quality_score,
                            "past_question_chars": len(repeat_ep.question),
                        },
                    )
                    if block:
                        block = block + "\n\n" + hint
                    else:
                        block = hint
            except Exception:
                # Re-ask detection must never abort the main loop.
                pass

        return block

    def _record_experience_memory(
        self,
        *,
        goal_description: str,
        question: str,
        answer: str,
        tools_used: list[str],
        source_labels: list[str],
        verified_chunks: int,
        unverified_chunks: int,
        replan_exhausted: bool,
        skip_consolidation: bool = False,
    ) -> None:
        """Write episodic/procedural/consolidation memory after a cycle.

        Experience memory is best-effort. A malformed local state file should
        be quarantined by the state layer, not crash the user-facing answer.
        """
        if (
            self.episodic_store is None
            and self.procedural_store is None
            and self.consolidation_store is None
        ):
            return
        try:
            episode = episode_from_agent_cycle(
                goal=goal_description,
                question=question,
                answer=answer,
                tools_used=tools_used,
                source_labels=source_labels,
                verified_chunks=verified_chunks,
                unverified_chunks=unverified_chunks,
                replan_exhausted=replan_exhausted,
            )
            if self.episodic_store is not None:
                self.episodic_store.save(episode)
                self.log.log(
                    "episodic_memory_write",
                    {
                        "episode_id": episode.id,
                        "outcome": episode.outcome,
                        "answer_quality_score": episode.answer_quality_score,
                        "tools_used": list(episode.tools_used),
                        "source_labels": list(episode.source_labels),
                        "verified_chunks": episode.verified_chunks,
                        "unverified_chunks": episode.unverified_chunks,
                    },
                )

            procedure = None
            if self.procedural_store is not None:
                procedure, created = self.procedural_store.upsert_from_episode(episode)
                self.log.log(
                    "procedural_memory_update",
                    {
                        "episode_id": episode.id,
                        "procedure_id": procedure.id if procedure else None,
                        "created": created,
                        "status": procedure.status if procedure else "skipped",
                        "confidence": procedure.confidence if procedure else None,
                    },
                )

            if (
                self.consolidation_store is not None
                and self.episodic_store is not None
                and self.procedural_store is not None
                and not skip_consolidation
            ):
                report = consolidate_memory(
                    episodes=self.episodic_store.load(),
                    procedures=self.procedural_store.load(),
                )
                self.consolidation_store.save(report)
                self.log.log(
                    "memory_consolidation",
                    {
                        "report_id": report.id,
                        "episode_count": report.episode_count,
                        "procedure_count": report.procedure_count,
                        "active_procedure_ids": list(report.active_procedure_ids),
                        "needs_review_procedure_ids": list(report.needs_review_procedure_ids),
                        "notes": list(report.notes),
                    },
                )
            elif skip_consolidation and self.consolidation_store is not None:
                self.log.log(
                    "memory_consolidation_skipped",
                    {"reason": "cheap_path"},
                )
        except Exception as exc:  # noqa: BLE001
            self.log.log(
                "smart_memory_error",
                {
                    "error": type(exc).__name__,
                    "message": str(exc),
                },
            )

    def smart_memory_summary(self) -> dict[str, Any]:
        """Return local smart-memory counts for operator CLI commands."""
        episodes = self.episodic_store.load() if self.episodic_store else []
        procedures = self.procedural_store.load() if self.procedural_store else []
        reports = self.consolidation_store.load() if self.consolidation_store else []
        last_report = reports[-1].to_dict() if reports else None
        return {
            "episodic": {
                "path": str(self.episodic_store.path) if self.episodic_store else None,
                "episodes": len(episodes),
                "outcomes": {
                    outcome: sum(1 for ep in episodes if ep.outcome == outcome)
                    for outcome in ("success", "partial", "failed")
                },
            },
            "procedural": {
                "path": str(self.procedural_store.path) if self.procedural_store else None,
                "procedures": len(procedures),
                "statuses": {
                    status: sum(1 for proc in procedures if proc.status == status)
                    for status in ("active", "needs_review", "obsolete")
                },
            },
            "consolidation": {
                "path": str(self.consolidation_store.path) if self.consolidation_store else None,
                "reports": len(reports),
                "last_report": last_report,
            },
        }

    # ---------- replan helpers ----------

    @staticmethod
    def _count_failures(history: list[ReplanTrigger]) -> dict[str, int]:
        """Return {failure_code: count} over the cumulative history.

        Used both by the audit log payload (`replan_attempt`) and the
        synthesizer prompt, so it's worth having one place that knows
        how to summarise the list.
        """
        counts: dict[str, int] = {}
        for t in history:
            counts[t.code] = counts.get(t.code, 0) + 1
        return counts

    @staticmethod
    def _format_replan_context(
        failure_history: list[ReplanTrigger],
        attempt: int,
        max_attempts: int,
        advice: str = "",
        forbidden_actions: tuple[tuple[str, str], ...] = (),
    ) -> str:
        """Build the <replan_context> block fed to the planner.

        Empty string when there's nothing to replan against (first attempt
        and no prior failures). Otherwise a compact XML block listing each
        failed step with its code, tool, arguments, and reason — exactly
        the shape the planner's system prompt has been told to consume.

        MVP-12 additions:
          - `advice` is the `ReplanDecision.advice_for_planner` string
            composed from per-FailureType budgets. It tells the planner
            what KIND of correction to make.
          - `forbidden_actions` lists (tool, args_json) pairs the planner
            must NOT propose again. Surfaced inline so the LLM can read
            and respect it; the sanitiser also enforces it as defence in
            depth.

        We do NOT redact secrets here because:
          - tool arguments came from the planner's previous output, which
            was itself redacted before going to the LLM, so they cannot
            contain raw secrets that the planner hadn't already seen;
          - `LLMPlanner.plan` runs `redact_text` over the assembled user
            prompt one more time as a safety net.
        """
        if not failure_history and not advice and not forbidden_actions:
            return ""
        lines = [
            f"<replan_context attempt=\"{attempt}\" "
            f"max_attempts=\"{max_attempts}\">"
        ]
        if failure_history:
            lines.append(
                f"  Previous attempts produced {len(failure_history)} failed "
                f"step(s). Pick a different approach."
            )
            for trig in failure_history:
                args_compact = json.dumps(trig.arguments, ensure_ascii=False)
                lines.append(
                    f"  - attempt={trig.attempt} "
                    f"code={trig.code} "
                    f"tool={trig.tool_name or '(none)'} "
                    f"arguments={args_compact}"
                )
                lines.append(f"    reason: {trig.reason}")
        if advice:
            lines.append("  <advice>")
            for advice_line in advice.splitlines():
                if advice_line.strip():
                    lines.append(f"    {advice_line}")
            lines.append("  </advice>")
        if forbidden_actions:
            lines.append(
                "  <forbidden>  # do NOT propose these exact (tool, arguments) pairs"
            )
            for tool, args_json in forbidden_actions:
                lines.append(f"    - tool={tool} arguments={args_json}")
            lines.append("  </forbidden>")
        lines.append("</replan_context>")
        return "\n".join(lines)

    # ---------- phase implementations ----------

    def _interpret(self, observation: Observation) -> Goal:
        question = observation.content["question"]
        return Goal(
            description=f"Answer the question: {question}",
            success_criteria="A grounded answer citing every claim back to a provided source.",
        )

    def _check_clarification(self, user_question: str) -> "ClarificationResult":
        """Run the Clarification Policy (§3) — pure heuristic, no LLM."""
        from core.clarification_policy import ClarificationResult, check_clarification
        return check_clarification(user_question)

    def _check_operational_domain(self, user_question: str) -> "DomainResult":
        """Run the Operational Design Domain gate (§7 ODD) — pure, no LLM."""
        from core.operational_domain import DomainResult, check_operational_domain
        return check_operational_domain(user_question)

    def _build_plan(self, goal: Goal, sources: list[dict[str, Any]]) -> Plan:
        plan = Plan(goal_id=goal.id)
        for i, src in enumerate(sources, start=1):
            plan.steps.append(  # pylint: disable=no-member  # pydantic list field, real list at runtime
                PlanStep(
                    plan_id=plan.id,
                    order=i,
                    action_spec={
                        "type": "tool_call",
                        "tool_name": src["tool"],
                        "arguments": src["arguments"],
                        "source_label": src["label"],
                    },
                    expected_outcome=src["expected_outcome"],
                )
            )
        plan.status = "in_progress"
        return plan

    def _force_file_hint_read_when_explicit(
        self,
        planner_out: PlannerOutput,
        *,
        question: str,
        file_hint: str | None,
    ) -> PlannerOutput:
        if not file_hint:
            return planner_out
        if any(src.get("tool") == "file_read" for src in planner_out.sources):
            return planner_out
        if not self._explicitly_requests_hinted_file(question):
            return planner_out
        sources = list(planner_out.sources)
        sources.append(self._file_hint_source(file_hint))
        warnings = list(planner_out.warnings)
        warnings.append(
            "explicit file-read request used --file hint because planner selected no file_read"
        )
        return PlannerOutput(
            reasoning=(
                f"{planner_out.reasoning} "
                "Kernel added read-only file_read for explicit hinted-file request."
            ).strip(),
            sources=sources,
            raw_response=planner_out.raw_response,
            warnings=warnings,
        )

    @staticmethod
    def _explicitly_requests_hinted_file(question: str) -> bool:
        text = " ".join(question.casefold().split())
        return any(
            phrase in text
            for phrase in (
                "прочитай файл",
                "прочти файл",
                "прочитать файл",
                "проанализируй файл",
                "анализ файла",
                "файл задания",
                "task file",
                "read the file",
                "read this file",
                "analyze the file",
            )
        )

    @staticmethod
    def _file_hint_source(file_hint: str) -> dict[str, Any]:
        return {
            "tool": "file_read",
            "arguments": {"path": file_hint},
            "label": f"file:{file_hint}",
            "expected_outcome": "Non-empty UTF-8 text from the hinted file.",
        }

    def _prepare_multi_file_review(
        self,
        question: str,
        *,
        file_hint: str | None,
    ) -> dict[str, Any]:
        requested_paths = self._extract_path_mentions(question)
        if len(requested_paths) < 2 or not self._is_file_review_request(question):
            return {"kind": "none"}

        explicit_mode = self._is_explicit_multi_file_mode(question)
        if file_hint and not explicit_mode:
            hint_norm = self._normalize_path_mention(file_hint)
            extra_paths = [
                path
                for path in requested_paths
                if self._normalize_path_mention(path) != hint_norm
            ]
            if extra_paths:
                available = file_hint
                extras = ", ".join(extra_paths)
                return {
                    "kind": "refusal",
                    "message": (
                        "Regular --file mode only permits the hinted file. "
                        f"Available evidence: {available}. "
                        f"Requested additional files were not reviewed: {extras}. "
                        "Use :ingest-source for additional files or explicit "
                        "multi-file review mode."
                    ),
                    "requested_paths": requested_paths,
                    "file_hint": file_hint,
                    "extra_paths": extra_paths,
                }
            return {"kind": "none"}

        if not explicit_mode and file_hint:
            return {"kind": "none"}
        if not explicit_mode and not self._looks_like_multi_file_review_without_hint(question):
            return {"kind": "none"}

        root = self._file_read_workspace_root()
        if root is None:
            return {
                "kind": "refusal",
                "message": (
                    "Multi-file review is unavailable because file_read is not "
                    "registered in this agent session."
                ),
                "requested_paths": requested_paths,
            }

        valid_sources: list[dict[str, Any]] = []
        warnings: list[str] = []
        seen_targets: set[str] = set()
        rejected: list[dict[str, str]] = []
        for raw_path in requested_paths:
            item = self._validate_user_file_path(raw_path, workspace=root)
            if item["ok"] is not True:
                rejected.append({
                    "path": raw_path,
                    "reason": str(item["reason"]),
                })
                warnings.append(f"{raw_path}: {item['reason']}")
                continue
            target_key = str(item["target"]).casefold()
            if target_key in seen_targets:
                warnings.append(f"{raw_path}: duplicate path skipped")
                continue
            seen_targets.add(target_key)
            rel_path = item["relative_path"].as_posix()
            valid_sources.append({
                "tool": "file_read",
                "arguments": {"path": rel_path},
                "label": f"file:{rel_path}",
                "expected_outcome": "Non-empty UTF-8 text from the explicitly mentioned file.",
            })

        if not valid_sources:
            details = "; ".join(
                f"{item['path']}: {item['reason']}" for item in rejected
            ) or "no valid files were mentioned"
            return {
                "kind": "refusal",
                "message": (
                    "Multi-file review could not start because no valid "
                    f"workspace files passed preflight. {details}."
                ),
                "requested_paths": requested_paths,
                "rejected": rejected,
            }

        self.log.log(
            "multi_file_review_preflight",
            {
                "requested_paths": requested_paths,
                "accepted_paths": [src["arguments"]["path"] for src in valid_sources],
                "rejected": rejected,
                "warnings": warnings,
            },
        )
        return {
            "kind": "forced",
            "sources": valid_sources,
            "warnings": warnings,
            "rejected": rejected,
            "reasoning": (
                "Kernel explicit multi-file review: read only the user-mentioned "
                "workspace files that passed strict path preflight. "
                + ("Rejected/skipped: " + "; ".join(warnings) if warnings else "")
            ),
        }

    @staticmethod
    def _is_file_review_request(question: str) -> bool:
        text = " ".join(question.casefold().split())
        return any(
            term in text
            for term in (
                "review",
                "read",
                "compare",
                "check",
                "проверь",
                "проверить",
                "прочитай",
                "прочти",
                "прочитать",
                "сравни",
                "сравнить",
                "проанализируй",
                "анализ",
            )
        )

    @staticmethod
    def _is_explicit_multi_file_mode(question: str) -> bool:
        text = " ".join(question.casefold().split())
        return any(
            term in text
            for term in (
                "explicit multi-file",
                "multi-file review",
                "multi file review",
                "multi-file read",
                "multi file read",
                "режим нескольких файлов",
                "несколько файлов",
                "многофайлов",
            )
        )

    @staticmethod
    def _looks_like_multi_file_review_without_hint(question: str) -> bool:
        text = " ".join(question.casefold().split())
        return any(
            term in text
            for term in (
                "сравни",
                "сравнить",
                "compare",
                "review these files",
                "read these files",
            )
        )

    def _file_read_workspace_root(self) -> Path | None:
        try:
            tool = self.registry.get("file_read")
        except KeyError:
            return None
        root = getattr(tool, "workspace_root", None)
        if root is None:
            return None
        return Path(root).resolve()

    def _validate_user_file_path(self, raw_path: str, *, workspace: Path) -> dict[str, Any]:
        cleaned = raw_path.strip().strip("\"'")
        normalized = cleaned.replace("\\", "/")
        if re.match(r"^[A-Za-z]:/", normalized):
            return {"ok": False, "reason": "absolute path escapes workspace"}
        path = Path(normalized)
        if any(part == ".." for part in path.parts):
            return {"ok": False, "reason": "path traversal is not allowed"}
        target = path.resolve() if path.is_absolute() else (workspace / path).resolve()
        try:
            target.relative_to(workspace)
        except ValueError:
            return {"ok": False, "reason": "absolute path escapes workspace"}
        if not target.exists():
            return {"ok": False, "reason": "missing file"}
        if target.is_dir():
            return {"ok": False, "reason": "directories require explicit ingestion command"}
        if not target.is_file():
            return {"ok": False, "reason": "not a regular file"}
        size = target.stat().st_size
        if size > FILE_READ_MAX_BYTES:
            return {
                "ok": False,
                "reason": f"file too large ({size} bytes > {FILE_READ_MAX_BYTES})",
            }
        rel_path = target.relative_to(workspace)
        return {
            "ok": True,
            "target": target,
            "relative_path": rel_path,
        }

    # ------------------------------------------------------------------
    # Parallel step execution helpers
    # ------------------------------------------------------------------

    def _run_step_parallel(
        self, step: PlanStep
    ) -> tuple[PlanStep, dict[str, Any] | None, "ReplanTrigger | None"]:
        """Thread-safe wrapper: runs _execute_step and returns (step, outcome, trigger).

        Clears the thread-local trigger slot before calling _execute_step so that
        each thread starts with a clean slate. Can be submitted safely to a
        ThreadPoolExecutor worker.
        """
        _step_trigger_tls.step_trigger = None
        outcome = self._execute_step(step)
        trigger = getattr(_step_trigger_tls, "step_trigger", None)
        _step_trigger_tls.step_trigger = None  # consume
        return step, outcome, trigger

    def _execute_steps_parallel(
        self, steps: list["PlanStep"]
    ) -> list[tuple["PlanStep", dict[str, Any] | None, "ReplanTrigger | None"]]:
        """Execute a list of plan steps, running independent steps in parallel.

        Steps whose ``preconditions`` list is empty (the common case — the
        planner currently never fills it) are all considered independent and
        run concurrently in a ThreadPoolExecutor. Steps that declare a
        precondition referencing another step in the same batch are run
        sequentially after the parallel group completes.

        Returns results in plan order: [(step, outcome, trigger), ...].
        """
        if not steps:
            return []
        if len(steps) == 1:
            return [self._run_step_parallel(steps[0])]

        # Partition: parallel = no intra-batch dependencies; sequential = rest.
        step_ids = {s.id for s in steps}
        parallel = [s for s in steps if not any(pc in step_ids for pc in s.preconditions)]
        sequential = [s for s in steps if any(pc in step_ids for pc in s.preconditions)]

        results: list[tuple[PlanStep, dict[str, Any] | None, ReplanTrigger | None]] = []

        if len(parallel) > 1:
            max_workers = min(len(parallel), 4)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(self._run_step_parallel, step): step
                    for step in parallel
                }
                for future in as_completed(futures):
                    results.append(future.result())
        else:
            for step in parallel:
                results.append(self._run_step_parallel(step))

        # Sequential steps follow in plan order.
        for step in sequential:
            results.append(self._run_step_parallel(step))

        # Re-sort to plan order so callers process artifacts in a stable sequence.
        order_map = {s.id: i for i, s in enumerate(steps)}
        results.sort(key=lambda r: order_map.get(r[0].id, 9999))
        return results

    def _execute_step(self, step: PlanStep) -> dict[str, Any] | None:
        """Run a single PlanStep through Act -> Policy -> Tool -> Verify.

        Working Memory short-circuit: if (tool, arguments) is already in the
        artifact cache, we skip Policy + Tool + Verify and reuse the cached
        output. Read-only tools are deterministic enough that this is safe
        within a single session.

        Returns artifact dict on success, None on hard failure. On failure
        the method also writes a `ReplanTrigger` to `_step_trigger_tls.step_trigger`
        so the parent loop can decide whether to re-plan. Using thread-local
        storage makes this safe to call from a ThreadPoolExecutor worker.
        """
        # Clear the scratch slot so the caller never sees a stale trigger
        # from a previous step within the same attempt.
        _step_trigger_tls.step_trigger = None

        tool_name = step.action_spec.get("tool_name")
        source_label = step.action_spec.get("source_label") or f"step:{step.id}"
        arguments = step.action_spec.get("arguments", {})

        # MAST FM-1.3 — track (tool, args) repetition across the whole run.
        # Logged only the first time the count crosses the threshold so the
        # event remains a single signal, not a per-replan stream.
        _tracker = getattr(self, "_step_repetition", None)
        if _tracker is not None and tool_name:
            _rep = _tracker.observe(tool_name, arguments)
            if _rep is not None:
                self.log.log("step_repetition_detected", _rep)

        # Memory cache short-circuit
        if self.memory is not None and tool_name:
            cached = self.memory.cache_lookup(tool_name, arguments)
            if cached is not None:
                self.log.log(
                    "memory_cache_hit",
                    {
                        "tool": tool_name,
                        "arguments": arguments,
                        "label": cached["label"],
                        "stored_at_turn": cached["turn_index"],
                    },
                )
                return {
                    "label": cached["label"],
                    "tool": tool_name,
                    "arguments": arguments,
                    "output": cached["output"],
                    "issues": ["served from working-memory cache"],
                }

        action = Action(
            step_id=step.id,
            type=step.action_spec["type"],
            tool_name=tool_name,
            parameters=arguments,
            side_effects="read" if tool_name else "none",
        )
        self.log.log("act", action, source_label=source_label)

        # Policy Gate — pre-execution checkpoint
        decision = self.policy.check(action)
        self.log.log("policy", decision)

        if decision.decision == "deny":
            err = ErrorObject(
                source="policy",
                code="policy_blocked",
                message=f"Action blocked: {', '.join(decision.reasons)}",
                severity="error",
                recoverable=False,
                context={
                    "action_id": action.id,
                    "decision": "deny",
                    "source_label": source_label,
                },
            )
            self.log.log("error", err)
            _step_trigger_tls.step_trigger = ReplanTrigger(
                code="policy_blocked",
                step_id=step.id,
                tool_name=tool_name,
                arguments=arguments,
                reason=f"Policy gate denied: {', '.join(decision.reasons) or 'unknown'}",
                attempt=self._current_attempt,
            )
            return None

        if decision.decision == "escalate":
            # PolicyGate flagged a risky action. Bring in the human via the
            # ApprovalProvider; if no provider is wired, the safe default
            # is to refuse — exactly the pre-MVP-6 behaviour.
            verdict = self._request_approval(
                action=action,
                step=step,
                source_label=source_label,
                policy_decision=decision,
                arguments=arguments,
            )
            if verdict != "approve":
                # Map the approval-side verdict back to a replan code
                # so the planner can pick a safer alternative (e.g.
                # swap an irreversible action for a read-only one).
                code: ReplanCode = (
                    "approval_deny" if verdict == "deny"
                    else "approval_abort" if verdict == "abort"
                    else "approval_unavailable"
                )
                # PolicyGate would have denied an unknown tool before we
                # got here, so the registry lookup is safe.
                risk = (
                    self.registry.get(tool_name).risk if tool_name else "unknown"
                )
                _step_trigger_tls.step_trigger = ReplanTrigger(
                    code=code,
                    step_id=step.id,
                    tool_name=tool_name,
                    arguments=arguments,
                    reason=f"Approval gate returned '{verdict}' for risk={risk}",
                    attempt=self._current_attempt,
                )
                return None

        if action.type != "tool_call":
            _step_trigger_tls.step_trigger = ReplanTrigger(
                code="unknown",
                step_id=step.id,
                tool_name=tool_name,
                arguments=arguments,
                reason=f"Unsupported action type: {action.type}",
                attempt=self._current_attempt,
            )
            return None  # not used in MVP-2

        # Tool execution
        result = self._call_tool(action)
        if result.status != "success":
            raw_error = result.error or "tool execution failed"
            # Detect file-absence specifically: FileNotFoundError (or
            # IsADirectoryError) means the file simply does not exist on
            # disk. This is a hard stop — the planner must NOT retry the
            # same path, search the web, or recreate the file from
            # general knowledge. Use a dedicated FailureType so the
            # replan policy communicates this clearly.
            _file_absent_prefixes = ("FileNotFoundError:", "IsADirectoryError:")
            if any(raw_error.startswith(p) for p in _file_absent_prefixes):
                replan_code: ReplanCode = "file_not_found"
            else:
                replan_code = "tool_error"
            err = ErrorObject(
                source=action.tool_name or "tool",
                code=replan_code,
                message=raw_error,
                severity="error",
                recoverable=False,
                context={"tool_call_id": result.tool_call_id, "source_label": source_label},
            )
            self.log.log("error", err)
            _step_trigger_tls.step_trigger = ReplanTrigger(
                code=replan_code,
                step_id=step.id,
                tool_name=tool_name,
                arguments=arguments,
                reason=raw_error,
                attempt=self._current_attempt,
            )
            return None

        # Tool Result Validation (delegates to the tool itself)
        tool = self.registry.get(action.tool_name)  # type: ignore[arg-type]
        is_ok, issues = tool.validate_output(result.output)
        self.log.log(
            "verify",
            {
                "step_id": step.id,
                "ok": is_ok,
                "issues": issues,
                "source_label": source_label,
            },
        )
        if not is_ok:
            err = ErrorObject(
                source="verifier",
                code="verify_failed",
                message=f"Tool Result Validation failed: {', '.join(issues) or 'unspecified'}",
                severity="error",
                recoverable=True,
                context={"step_id": step.id, "source_label": source_label},
            )
            self.log.log("error", err)
            _step_trigger_tls.step_trigger = ReplanTrigger(
                code="verify_failed",
                step_id=step.id,
                tool_name=tool_name,
                arguments=arguments,
                reason=f"Validation rejected output: {'; '.join(issues) or 'no detail'}",
                attempt=self._current_attempt,
            )
            return None

        # --- MVP-12: structured-failure reclassification ----------------------
        # Two outcomes pass validate_output but still need a re-plan with a
        # specific advice: empty web_search results and shell_exec timeouts.
        # Detecting them HERE (not inside the tools) keeps the tool contracts
        # stable while letting the ReplanPolicy advise the planner per case.
        if action.tool_name == "web_search" and isinstance(result.output, list) and len(result.output) == 0:
            self.log.log(
                "error",
                ErrorObject(
                    source="web_search",
                    code="web_empty",
                    message="web_search returned zero hits — query needs reformulation",
                    severity="error",
                    recoverable=True,
                    context={"step_id": step.id, "source_label": source_label},
                ),
            )
            _step_trigger_tls.step_trigger = ReplanTrigger(
                code="web_empty",
                step_id=step.id,
                tool_name=tool_name,
                arguments=arguments,
                reason="web_search returned 0 results; reformulate the query",
                attempt=self._current_attempt,
            )
            return None
        if action.tool_name == "shell_exec" and isinstance(result.output, dict) and result.output.get("timed_out"):
            self.log.log(
                "error",
                ErrorObject(
                    source="shell_exec",
                    code="timeout",
                    message="shell_exec hit its timeout — output truncated, exit_code unknown",
                    severity="error",
                    recoverable=True,
                    context={"step_id": step.id, "source_label": source_label},
                ),
            )
            _step_trigger_tls.step_trigger = ReplanTrigger(
                code="timeout",
                step_id=step.id,
                tool_name=tool_name,
                arguments=arguments,
                reason="shell_exec timed out; reduce scope or pick a faster path",
                attempt=self._current_attempt,
            )
            return None

        # --- Safety pipeline (§7 / MVP-7) ---------------------------------
        # 1. Classify the tool output.
        # 2. Emit secret_detected when the classifier returns SECRET.
        # 3. Deep-redact the output BEFORE it reaches artifacts, memory
        #    cache, or the synthesizer prompt. From this point on the loop
        #    only handles redacted text.
        #
        # Outer guard: classification / redaction helpers should never raise,
        # but if they do (e.g. a corrupted output string), we capture the
        # exception as an "unknown" replan trigger so the plan can retry
        # rather than crashing the entire cycle.
        try:
            source_hint: SourceHint = _TOOL_SOURCE_HINTS.get(
                action.tool_name or "", "tool_output"
            )
            flat_output = _to_text(result.output)
            cls_result = classify(flat_output, source=source_hint)
            self.log.log(
                "data_classified",
                {
                    "label": source_label,
                    "tool": action.tool_name,
                    "class": cls_result.cls.value,
                    "source": cls_result.source,
                    "reasons": cls_result.reasons,
                },
            )

            # --- Injection Guard (§2 Adversarial Defence) ------------------
            # Scan tool output for indirect prompt-injection patterns BEFORE
            # the content reaches the synthesizer prompt.  Blocked content is
            # dropped and triggers a replan; suspicious content is annotated
            # and passed through with a trust warning.
            # Internal workspace tools (file_read, list_dir, …) are exempt:
            # their content originates inside the trusted boundary and
            # scanning them produces false-positives with no security benefit.
            if action.tool_name in _TRUSTED_INTERNAL_TOOLS:
                inj = None
            else:
                inj = scan_for_injection(flat_output)
            if inj is not None and inj.verdict != "clean":
                self.log.log(
                    "injection_" + inj.verdict,
                    {
                        "label": source_label,
                        "tool": action.tool_name,
                        **inj.to_log_payload(),
                    },
                )
            if inj is not None and inj.is_blocked:
                _step_trigger_tls.step_trigger = ReplanTrigger(
                    code="injection_blocked",
                    step_id=step.id,
                    tool_name=tool_name,
                    arguments=arguments,
                    reason=(
                        "Tool output blocked by injection guard "
                        f"({len(inj.findings)} finding(s)). "
                        "Try a different source or query."
                    ),
                    attempt=self._current_attempt,
                )
                return None
            if inj is not None and inj.verdict == "suspicious":
                # Wrap the output with a trust-warning annotation so the
                # synthesizer knows the content may be adversarial.
                annotated = annotate_suspicious(flat_output, source_label)
                result = result.model_copy(update={"output": annotated})
                flat_output = annotated
            # ---------------------------------------------------------------

            if cls_result.cls == DataClass.SECRET:
                findings = scan(flat_output)
                kinds = sorted({f.kind for f in findings})
                self.log.log(
                    "secret_detected",
                    {
                        "label": source_label,
                        "tool": action.tool_name,
                        "kinds": kinds,
                        "count": len(findings),
                        "surface": "tool_output",
                    },
                )
                if self._cycle_findings is not None:
                    self._cycle_findings.append(
                        {"label": source_label, "kinds": kinds, "count": len(findings)}
                    )

                # Redact SECRET output before it reaches the planner,
                # synthesizer, or memory cache.
                safe_output = redact_payload(result.output)

                if self.memory is not None and action.tool_name:
                    self.memory.cache_store(
                        tool_name=action.tool_name,
                        arguments=arguments,
                        output=safe_output,
                        label=source_label,
                    )

                return {
                    "label": source_label,
                    "tool": action.tool_name,
                    "arguments": action.parameters,
                    "output": safe_output,
                    "issues": issues,
                    "data_class": cls_result.cls.value,
                }
            elif cls_result.cls == DataClass.SENSITIVE:
                findings = collect_pii_findings(flat_output)
                kinds = sorted({f"pii-{f.kind}" for f in findings})
                self.log.log(
                    "sensitive_detected",
                    {
                        "label": source_label,
                        "tool": action.tool_name,
                        "kinds": kinds,
                        "count": len(findings),
                        "surface": "tool_output",
                    },
                )
                if self._cycle_findings is not None:
                    self._cycle_findings.append(
                        {"label": source_label, "kinds": kinds, "count": len(findings)}
                    )

                safe_output = redact_payload(result.output)

                # Memory write — cache the REDACTED artifact for future turns
                if self.memory is not None and action.tool_name:
                    self.memory.cache_store(
                        tool_name=action.tool_name,
                        arguments=arguments,
                        output=safe_output,
                        label=source_label,
                    )

                return {
                    "label": source_label,
                    "tool": action.tool_name,
                    # MVP-14.1: surfaced so the evidence factory can build a
                    # typed `Evidence` (source_id often depends on an argument
                    # like `path` or `url`).
                    "arguments": action.parameters,
                    "output": safe_output,
                    "issues": issues,
                    "data_class": cls_result.cls.value,
                }

            # PUBLIC / INTERNAL / SECRET-flagged data: cache original output
            # and return it to the planner. For SECRET data the loop already
            # emitted a `secret_detected` log event above; the output is
            # returned as-is so the planner can still act on it.
            if self.memory is not None and action.tool_name:
                self.memory.cache_store(
                    tool_name=action.tool_name,
                    arguments=arguments,
                    output=result.output,
                    label=source_label,
                )

            return {
                "label": source_label,
                "tool": action.tool_name,
                "arguments": action.parameters,
                "output": result.output,
                "issues": issues,
                "data_class": cls_result.cls.value,
            }

        except Exception as exc:  # noqa: BLE001
            # Last-resort guard: if any postprocessing step (classify,
            # redact, cache_store) raises unexpectedly, capture it as an
            # "unknown" replan trigger rather than crashing the loop.
            self.log.log(
                "error",
                ErrorObject(
                    source="loop",
                    code="unknown",
                    message=f"Postprocessing error in _execute_step: {type(exc).__name__}: {exc}",
                    severity="error",
                    recoverable=True,
                    context={"step_id": step.id, "source_label": source_label},
                ),
            )
            _step_trigger_tls.step_trigger = ReplanTrigger(
                code="unknown",
                step_id=step.id,
                tool_name=tool_name,
                arguments=arguments,
                reason=f"Postprocessing error: {type(exc).__name__}: {exc}",
                attempt=self._current_attempt,
            )
            return None

    def _call_tool(self, action: Action) -> ToolResult:
        assert action.tool_name is not None
        tool = self.registry.get(action.tool_name)
        call = ToolCall(
            action_id=action.id,
            tool_name=action.tool_name,
            arguments=action.parameters,
        )
        self.log.log("tool_call", call)
        result = tool.invoke(call)
        self.log.log("tool_result", result, status=result.status, latency_ms=result.latency_ms)
        # MVP-11 Compensation capture: a successful tool call may carry a
        # `compensation_plan` block in its structured output. The agent
        # owns the plan registry from here on — `rollback()` reads from
        # this list, in LIFO order, and applies the plan inside the
        # workspace root sandbox.
        self._capture_compensation_plan(result)
        return result

    def _capture_compensation_plan(self, result: ToolResult) -> None:
        """Extract + register a `compensation_plan` from tool output, if any.

        Only success results are inspected. The plan is logged as a
        `compensation_registered` event so an audit can match every
        applied rollback to the tool call that produced it.
        """
        if result.status != "success" or not isinstance(result.output, dict):
            return
        raw = result.output.get("compensation_plan")
        if not isinstance(raw, dict):
            return
        # Skip noop plans — they pollute :rollback semantics and the
        # audit log. The plan is still IN the tool_result output, so
        # an analyst can see read-only commands also produced one.
        actions = raw.get("actions") or []
        if all(a.get("kind") == "noop" for a in actions):
            return
        from core.compensation import CompensationPlan  # avoid import cycle

        plan = CompensationPlan.from_dict(raw)
        self.compensation_log.append(plan)
        self.log.log(
            "compensation_registered",
            {
                "plan_id": plan.id,
                "tool_name": plan.tool_name,
                "description": plan.description,
                "action_count": len(plan.actions),
                "tool_call_id": result.tool_call_id,
            },
        )

    # ---------- approval ----------

    def _request_approval(
        self,
        action: Action,
        step: PlanStep,
        source_label: str,
        policy_decision: PolicyDecision,
        arguments: dict[str, Any],
    ) -> _Literal["approve", "deny", "abort", "unavailable"]:
        """Bridge between the PolicyGate's `escalate` verdict and the human.

        Emits two trace events — `approval_request` then `approval_decision`
        — and turns the decision into a control-flow signal for the loop:
          - "approve" -> caller continues with the tool call
          - anything else -> caller bails out, no tool_call is logged
        """
        if self.approval_provider is None:
            err = ErrorObject(
                source="approval",
                code="approval_unavailable",
                message=(
                    "Action escalated but no approval provider is configured. "
                    f"Reasons: {', '.join(policy_decision.reasons)}"
                ),
                severity="error",
                recoverable=False,
                context={
                    "action_id": action.id,
                    "policy_decision_id": policy_decision.id,
                    "source_label": source_label,
                },
            )
            self.log.log("error", err)
            return "unavailable"

        # Tool must exist — PolicyGate would have returned deny otherwise.
        tool = self.registry.get(action.tool_name)  # type: ignore[arg-type]
        # Mirror the PolicyGate: ask the tool for its argument-aware
        # risk so the approval prompt reports what the gate actually
        # decided on, not the static class fallback.
        effective_risk = tool.risk_for(arguments or {})
        request = ApprovalRequest(
            action_id=action.id,
            step_id=step.id,
            tool_name=action.tool_name,
            arguments=arguments,
            risk=effective_risk,
            reasons=list(policy_decision.reasons),
            summary=(
                f"Tool '{action.tool_name}' wants to execute "
                f"with risk={effective_risk}."
            ),
            policy_decision_id=policy_decision.id,
        )
        self.log.log("approval_request", request)

        decision = self.approval_provider.request(request)
        self.log.log("approval_decision", decision)

        if decision.decision == "approve":
            return "approve"

        err = ErrorObject(
            source="approval",
            code=f"approval_{decision.decision}",  # approval_deny | approval_abort
            message=(
                f"Approval not granted ({decision.decision}): "
                f"{'; '.join(decision.reasons) or 'no reason given'}"
            ),
            severity="error",
            recoverable=False,
            context={
                "action_id": action.id,
                "approval_request_id": request.id,
                "approval_decision_id": decision.id,
                "responder": decision.responder,
                "source_label": source_label,
            },
        )
        self.log.log("error", err)
        return decision.decision

    # ---------- response synthesis ----------

    def _synthesize(
        self,
        goal: Goal,
        artifacts: dict[str, dict[str, Any]],
        question: str,
        planner_reasoning: str,
        history: str = "",
        persistent_block: str = "",
        cycle_findings: list[dict[str, Any]] | None = None,
        failure_history: list[ReplanTrigger] | None = None,
        llm=None,
        lean_context: bool = False,
    ) -> str:
        history_block = (
            f"<conversation_history>\n{history}\n</conversation_history>\n\n"
            if history.strip()
            else ""
        )
        # Cheap path: drop the heavy, question-irrelevant injections
        # (long-term memory, user profile, run assumptions). A trivial
        # greeting / config-flag echo is answered from general knowledge;
        # these blocks only inflate the prompt token count.
        if lean_context:
            long_term_block = ""
            profile_block = ""
            assumptions_block = ""
            role_block = ""
        else:
            long_term_block = (
                f"{persistent_block}\n\n" if persistent_block.strip() else ""
            )
            role_block = self.last_role_context.to_prompt_block() + "\n\n"
            profile_block = (
                profile_to_prompt_block(self.last_user_profile) + "\n\n"
                if self.last_user_profile is not None
                else ""
            )
            # Layer 5 — inject active assumptions into synthesizer.
            _assumptions_src = getattr(self, "_run_assumptions_current", None)
            assumptions_block = (
                _assumptions_src.to_prompt_block() + "\n\n"
                if _assumptions_src is not None and len(_assumptions_src) > 0
                else ""
            )

        # Kernel-built safety notes — the LLM is told to surface these in
        # the user-facing answer. The notes describe what was redacted
        # (the kernel did it), not what the LLM did.
        safety_block = ""
        if cycle_findings:
            lines = ["<safety_notes>"]
            for f in cycle_findings:
                lines.append(
                    f"- label={f['label']} kinds={f['kinds']} "
                    f"count={f['count']} (kernel-redacted)"
                )
            lines.append("</safety_notes>")
            safety_block = "\n".join(lines) + "\n\n"

        # Failure context (MVP-8). Only injected when re-planning was
        # exhausted; carries the cumulative trigger list so the
        # synthesizer can write an honest Conclusion ("I tried X, Y, Z;
        # here is why none worked") instead of an empty/fake answer.
        failure_block = ""
        if failure_history:
            lines = ["<failure_context>"]
            lines.append(
                "Re-planning was exhausted after every attempt failed."
            )
            for trig in failure_history:
                lines.append(
                    f"- attempt={trig.attempt} code={trig.code} "
                    f"tool={trig.tool_name or '(none)'}: {trig.reason}"
                )
            lines.append("</failure_context>")
            failure_block = "\n".join(lines) + "\n\n"

        # The question travels into the LLM prompt; redact any credential
        # or sensitive PII the user pasted in.
        safe_question, _q_findings, _q_pii_findings = redact_dlp_text(question)

        if artifacts:
            from core.evidence_budget import apply_total_budget
            raw_blocks: list[tuple[str, str]] = []
            for label, art in artifacts.items():
                formatted = self._format_artifact(
                    art["tool"], art["output"], question=question
                )
                raw_blocks.append((label, formatted))

            # Apply total evidence budget — trim largest artifact first
            trimmed_blocks, was_trimmed = apply_total_budget(raw_blocks)
            if was_trimmed:
                self.log.log(
                    "evidence_budget_trim",
                    {
                        "labels": [lbl for lbl, _ in trimmed_blocks],
                        "total_chars": sum(len(c) for _, c in trimmed_blocks),
                    },
                )

            blocks: list[str] = [
                f'<evidence source="{lbl}">\n{content}\n</evidence>'
                for lbl, content in trimmed_blocks
            ]
            evidence = "\n\n".join(blocks)
            allowed_citations_block = self._format_allowed_citations_block(
                self.last_provenance
            )
            file_scope_notice = self._file_scope_notice(question, artifacts)
            file_scope_block = (
                "<file_scope_notice>\n"
                f"{file_scope_notice}\n"
                "Do not claim any unverified path exists or was read.\n"
                "</file_scope_notice>\n\n"
                if file_scope_notice
                else ""
            )

            warnings = [
                f"{label}: {'; '.join(art['issues'])}"
                for label, art in artifacts.items()
                if art.get("issues")
            ]
            warnings_block = (
                "<validator_notes>\n" + "\n".join(warnings) + "\n</validator_notes>\n\n"
                if warnings
                else ""
            )
            user_prompt = (
                f"{safety_block}"
                f"{failure_block}"
                f"{role_block}"
                f"{profile_block}"
                f"{assumptions_block}"
                f"{long_term_block}"
                f"{history_block}"
                f"{allowed_citations_block}"
                f"{file_scope_block}"
                f"{evidence}\n\n"
                f"{warnings_block}"
                f"planner_reasoning: {planner_reasoning}\n\n"
                f"Question: {safe_question}\n\n"
                "Answer using the Output Contract from the system instructions. "
                "Maintain continuity with any prior turns shown in conversation_history. "
                "If long_term_memory contains a relevant record you may cite it "
                "with source label [memory:<record_id>]."
            )
        else:
            # No tools were called — either the planner judged this a
            # general-knowledge question, OR a follow-up answerable from
            # conversation_history / long_term_memory alone, OR re-planning
            # exhausted (failure_history present). The Output Contract
            # still applies in every case.
            extra_guidance = ""
            if failure_history:
                extra_guidance = (
                    "Re-planning was exhausted. Use the <failure_context> "
                    "block to write an honest Conclusion: state plainly that "
                    "the agent could not collect evidence, list what was "
                    "tried (one bullet per attempt), and put the unmet "
                    "information need under Unverified. Cite each fact as "
                    "[general-knowledge] when relying on prior knowledge."
                )
            user_prompt = (
                f"{safety_block}"
                f"{failure_block}"
                f"{role_block}"
                f"{profile_block}"
                f"{assumptions_block}"
                f"{long_term_block}"
                f"{history_block}"
                f"planner_reasoning: {planner_reasoning}\n\n"
                f"Question: {safe_question}\n\n"
                "No <evidence> blocks are provided. If conversation_history or "
                "long_term_memory covers the answer, cite those sources verbatim "
                "(use [memory:<record_id>] for long_term_memory entries); "
                "otherwise answer from general knowledge with [general-knowledge] "
                "as the source label. Follow the Output Contract from the system instructions."
                + (f" {extra_guidance}" if extra_guidance else "")
            )

        # Defence-in-depth: the prompt is now built from redacted artifacts
        # and a redacted question. One more pass catches anything we missed
        # (e.g. a secret or PII hiding in planner_reasoning).
        safe_user_prompt, _secret_findings, _pii_findings = redact_dlp_text(user_prompt)
        _active_llm = llm if llm is not None else self.llm
        from core.planner import _build_host_tools_block  # lazy import — avoids circular
        host_block = _build_host_tools_block()
        # Inject host tools as a synthetic evidence block so the model treats
        # the paths as verified context (the model answers strictly from evidence).
        if host_block:
            host_evidence = (
                "\n\n<evidence source=\"host_tools\">\n"
                + host_block.strip()
                + "\nWhen a script was written for one of these tools, state the EXACT "
                "run command from above in the Facts section so the user knows how to execute it."
                + "\n</evidence>"
            )
            safe_user_prompt = safe_user_prompt + host_evidence
        _on_token = getattr(self, "_stream_on_token", None)
        if _on_token is not None:
            return _active_llm.stream_complete(
                system=SYSTEM_ANSWER, user=safe_user_prompt,
                temperature=0.5, on_token=_on_token,
            )
        return _active_llm.complete(system=SYSTEM_ANSWER, user=safe_user_prompt, temperature=0.5)

    @staticmethod
    def _citation_for_evidence(ev: Evidence) -> str | None:
        source_id = ev.source_id
        if ev.kind == "file" and source_id.startswith("file:"):
            body = source_id[len("file:"):]
            return f"[file:{body}]"
        if ev.kind == "web_page" and source_id.startswith("web_page:"):
            body = source_id[len("web_page:"):]
            return f"[web:{body}]"
        if ev.kind == "web_search_hit" and source_id.startswith("web_search:"):
            body = source_id[len("web_search:"):]
            return f"[search:{body}]"
        if ev.kind == "test_result" and source_id.startswith("test_result:"):
            body = source_id[len("test_result:"):]
            return f"[test:{body}]"
        if ev.kind == "log_event" and source_id.startswith("log_event:"):
            body = source_id[len("log_event:"):]
            return f"[log:{body}]"
        if ev.kind == "shell_output" and source_id.startswith("shell_output:"):
            body = source_id[len("shell_output:"):]
            return f"[shell:{body}]"
        if ev.kind == "tool_output" and source_id.startswith("tool_output:"):
            body = source_id[len("tool_output:"):]
            return f"[tool:{body}]"
        if ev.kind == "diff_preview" and source_id.startswith("diff_preview:"):
            body = source_id[len("diff_preview:"):]
            return f"[diff:{body}]"
        if ev.kind == "memory":
            return f"[memory:{source_id}]"
        if ev.kind == "user_explicit":
            return "[user]"
        return None

    @classmethod
    def _format_allowed_citations_block(cls, chain: ProvenanceChain) -> str:
        if not chain.evidences:
            return ""
        lines = ["<allowed_citations>"]
        seen: set[str] = set()
        for ev in chain.evidences:
            token = cls._citation_for_evidence(ev)
            if token is None or token in seen:
                continue
            seen.add(token)
            lines.append(
                f"- {token} kind={ev.kind} source_id={ev.source_id}"
            )
        lines.append("</allowed_citations>")
        return "\n".join(lines) + "\n\n" if len(lines) > 2 else ""

    @staticmethod
    def _format_artifact(tool_name: str | None, output: Any, *, question: str = "") -> str:
        """Render a tool output into a stable string the LLM can ground on.

        File content is passed through :func:`core.evidence_budget.budget_file_content`
        which applies the per-artifact character budget and performs intent-aware
        extraction: instead of blindly returning the first N chars, the most
        question-relevant paragraphs are selected (Realtime Intent Fix).
        """
        if tool_name == "web_search" and isinstance(output, list):
            if not output:
                return "(no results)"
            lines: list[str] = []
            for r in output:
                title   = r.get("title") or "(no title)"
                url     = r.get("url") or ""
                snippet = r.get("snippet") or ""
                source  = r.get("source") or "duckduckgo"
                lines.append(f"- {title}")
                lines.append(f"  url: {url}")
                if snippet:
                    lines.append(f"  snippet: {snippet}")
                lines.append(f"  provider: {source}")
            return "\n".join(lines)
        if tool_name == "file_read" and isinstance(output, str):
            from core.evidence_budget import budget_file_content
            return budget_file_content(output, question=question)
        if tool_name == "list_dir" and isinstance(output, str):
            return output
        # Fallback: stringify whatever came back.
        return str(output)

    @classmethod
    def _file_scope_notice(
        cls,
        question: str,
        artifacts: dict[str, dict[str, Any]],
    ) -> str:
        actual_paths = [
            label[len("file:"):]
            for label, art in artifacts.items()
            if label.startswith("file:") and art.get("tool") == "file_read"
        ]
        if not actual_paths:
            return ""
        actual_norms = {cls._normalize_path_mention(path) for path in actual_paths}
        requested_paths = cls._extract_path_mentions(question)
        missing = [
            path
            for path in requested_paths
            if cls._normalize_path_mention(path) not in actual_norms
        ]
        if not missing:
            return ""
        actual = ", ".join(actual_paths)
        unverified = ", ".join(missing)
        return (
            f"Evidence scope: I only have evidence for {actual}. "
            f"I did not verify {unverified}."
        )

    @staticmethod
    def _extract_path_mentions(text: str) -> list[str]:
        pattern = re.compile(
            r"(?P<path>"
            r"(?:[A-Za-z]:[\\/])?"
            r"(?:/)?"
            r"(?:\.{1,2}[\\/])?"
            r"(?:[A-Za-z0-9_.-]+[\\/])*"
            r"[A-Za-z0-9_.-]+\."
            r"(?:py|md|txt|json|yml|yaml|pdf)"
            r")",
            flags=re.IGNORECASE,
        )
        seen: set[str] = set()
        paths: list[str] = []
        for match in pattern.finditer(text):
            path = match.group("path").rstrip(".,;:!?)\"]}'")
            key = path.casefold()
            if key in seen:
                continue
            seen.add(key)
            paths.append(path)
        return paths

    @staticmethod
    def _normalize_path_mention(path: str) -> str:
        out = path.strip().strip("\"'")
        out = out.replace("/", "\\")
        while out.startswith(".\\"):
            out = out[2:]
        return out.casefold()


def new_trace_id() -> str:
    return new_id("run")

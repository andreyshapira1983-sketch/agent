"""Writes at most ONE low-risk ``self_apply_lane.run`` proposal into the
approval inbox, with full file content, for a human to bless.

Never applies it: no patch reaches the tree, no lane runs, nothing is
committed, pushed or merged, and the daemon, scheduler, agent_tick and the
budget/model/catalog config are never touched.

Roles: Manager -> Researcher -> Builder -> Critic -> Reporter.

Four hard gates run before any LLM work, first trip wins, and each names the
status it returns:

  budget kill-switch active     -> "budget_kill_switch"
  hour budget near exhaustion   -> "budget_wait"
  a self_apply approval pending -> "approval_wait"
  dirty git working tree        -> "dirty_tree_wait"

After them the Manager may find no candidate ("no_patch") and the Critic may
veto ("critic_veto"); either way no inbox item is created, and success is
"proposed". Every dependency is injected, so no real provider is reachable
from here.
"""
from __future__ import annotations

import ast
import hashlib
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.anatomy_sync import (  # noqa: F401 — re-exported for the split tests
    _ANATOMY_DOC_PATH,
    _new_core_module_stems,
    _sync_anatomy_groups,
    _sync_anatomy_index,
)
from core.backlog_target_mapper import map_backlog_candidate
from core.builder_reply_diagnosis import why_builder_reply_failed
from core.injection_guard import prepare_untrusted_text_for_llm
from core.plan_parsing import extract_json_object
from core.proposal_value_gate import evaluate_proposal_value
from core.redaction import prepare_text_for_llm_boundary
from core.self_apply_bridge import (
    DEFAULT_ROLLBACK,
    SELF_APPLY_OPERATION,
    build_self_apply_payload,
)
from core.self_apply_lane import FileChange, _normalize_rel, classify_patch_risk
from core.self_build_supervisor import (
    hour_budget_headroom,
    is_budget_near_exhaustion,
)

# Origin tag stamped on every payload this producer emits, so downstream tooling
# (and the TD-024 bridge) can tell an autonomously-produced proposal apart from
# a manual or repair-produced one.
PRODUCER_ORIGIN = "subagent_self_build_producer"

# Origin tag for a deterministic (no-LLM) incremental-split step. Shared by the
# ``:self-split`` command and the ``:self-build-produce`` producer so both emit
# an identical, already-trusted approval artifact for an oversized-module split.
INCREMENTAL_SPLIT_ORIGIN = "incremental_splitter"

# Seed allowlist of small low-risk candidate targets. Historically this was the
# ONLY set the Manager could pick from. As of Provod #2 it is just a seed: a
# grounded backlog candidate may also target any file that clears both hard
# safety layers (see ``_is_self_build_target_allowed``) — the critical-organ
# denylist below and the self-apply lane's low-risk classifier. Apply authority
# is unchanged: every patch is still dry-run + human-approved + test-gated.
DEFAULT_CANDIDATE_TARGETS: tuple[str, ...] = (
    "core/redaction.py",
    "core/truth_hype_filter.py",
    "docs/self_build.md",
)
_SELF_BUILD_DOC_TARGET = "docs/self_build.md"

# Critical "organs" that must never be a producer target in this first PR, even
# though some would otherwise pass the lane allowlist. Denylist wins before the
# allowlist. Prefix entries (ending in "/") match any file beneath them.
CRITICAL_DENY: tuple[str, ...] = (
    "main.py",
    "core/loop.py",
    "core/autonomous_runtime.py",
    "core/model_usage.py",
    "core/safe_vcs.py",
    "core/self_apply_lane.py",
    "core/self_apply_bridge.py",
    "core/self_build_producer.py",
    "AGENT_DOCTRINE.md",
    "docs/AGENT_DOCTRINE.md",
    "docs/COGNITIVE_CORE.md",
    "config/",
)

# Upper bound on generated file size. A low-risk self-build patch is small; a
# huge blob is a signal of a runaway generation, not a surgical change.
_MAX_CONTENT_BYTES = 60_000

# The Builder must return the FULL post-image of the target file, not a diff. A
# 2000-token cap truncates any non-trivial file (the JSON is cut off and fails to
# parse, which the Critic then vetoes as "empty generated content"). This gives
# enough headroom for real files while staying under _MAX_CONTENT_BYTES.
_BUILDER_MAX_TOKENS = 16_000

# The repo enforces (scripts/agent_anatomy_check.py) that every core/*.py module
# is referenced as a ``core/<name>`` token in this index. A module split creates
# new core modules, so the head keeps this doc in sync automatically.

_DEFAULT_CONFIDENCE_THRESHOLD = 0.6

# A single-shot Builder cannot safely split a very large module — it keeps
# dropping public API and getting vetoed. Above this many lines a ``split_module``
# candidate is refused up-front ("too large for a single-shot split") instead of
# burning a doomed generation; it needs a human-scoped incremental split.
_MAX_SPLIT_TARGET_LINES = 900

# Default number of Builder attempts per run. ``1`` preserves the historical
# single-shot behaviour (and every existing producer test). The real
# ``:self-build-produce`` command opts into ``2`` so ONE Critic veto is retried
# with the exact veto reasons fed back before the run gives up — a vetoed
# candidate is still NEVER applied.
_DEFAULT_MAX_BUILDER_ATTEMPTS = 1

# Lines/markers that betray a diff/patch instead of full file content. The
# Builder must return the whole post-image; a diff-only answer is vetoed.
_DIFF_MARKERS = ("--- ", "+++ ", "@@ ", "diff --git", "index ")

_SELF_BUILD_DOC_REQUIRED_TOPICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("self-build produce command", (":self-build-produce",)),
    ("approval item only", ("approval item", "approval inbox")),
    ("human approval inspection", (":approval-list", ":approval-triage", "inspect")),
    (
        "approve or deny decision",
        (":approval-approve", ":approval-deny", "approve", "deny"),
    ),
    ("separate self-apply run", (":self-apply-run", "separate")),
    ("no auto-apply", ("no auto-apply",)),
    ("no auto-commit", ("no auto-commit",)),
    ("scheduler and allow-effects boundary", ("scheduler", "allow-effects")),
    ("blocked autonomy tracks", ("g6a", "gateway", "runner", "remote git")),
    ("safe operator checklist", ("checklist",)),
    ("reject criteria", ("reject", "deny if")),
)

_SELF_BUILD_DOC_GUIDANCE = (
    "For docs/self_build.md specifically, write an operator guide for this "
    "repo's human-gated self-build loop, not a generic build-from-source or "
    "installation guide. It must explain: :self-build-produce creates one "
    "approval item only; the human inspects approval items with :approval-list "
    "or :approval-triage; the human approves or denies with :approval-approve "
    "or :approval-deny; :self-apply-run is separate and requires explicit "
    "human approval; no auto-apply; no auto-commit; no scheduler or "
    "allow-effects; no G6a, gateway changes, runner work, or remote git; a "
    "safe operator checklist; and reject criteria for bad proposals."
)


# ── data ────────────────────────────────────────────────────────────────────


@dataclass
class RoleOutput:
    """One structured role result, surfaced verbatim in the ProducerReport so an
    operator can see *who decided what*."""

    role: str
    decision: str
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "decision": self.decision,
            "detail": self.detail,
            "data": dict(self.data),
        }


@dataclass
class ProducerReport:
    status: str
    reason: str = ""
    target_path: str | None = None
    approval_id: str | None = None
    checked_gates: list[str] = field(default_factory=list)
    role_outputs: list[RoleOutput] = field(default_factory=list)
    veto_reasons: list[str] = field(default_factory=list)
    value_flags: list[str] = field(default_factory=list)
    attempts: int = 1
    next_human_action: str = ""
    #: What the pipeline actually READ (MIR-121's write side): `file:<path>`
    #: for the target, `memory:self-build-lessons` when recalled lessons were
    #: injected into the Builder prompt. The episode writer carries these into
    #: `source_labels`, so lesson provenance is recorded where the reading
    #: happens rather than reconstructed later.
    sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "target_path": self.target_path,
            "approval_id": self.approval_id,
            "checked_gates": list(self.checked_gates),
            "roles": [r.to_dict() for r in self.role_outputs],
            "veto_reasons": list(self.veto_reasons),
            "value_flags": list(self.value_flags),
            "attempts": self.attempts,
            "next_human_action": self.next_human_action,
            "sources": list(self.sources),
        }


# ── helpers ─────────────────────────────────────────────────────────────────


def _looks_like_diff(content: str) -> bool:
    for line in content.splitlines():
        stripped = line.lstrip()
        if any(stripped.startswith(marker) for marker in _DIFF_MARKERS):
            return True
    return False


def _top_level_defined_names(tree: ast.Module) -> set[str]:
    """Names *defined* at module top level (functions, classes, assignments).

    These form the import surface of the module: anything another module or a
    test can reach via ``from core.<mod> import <name>``. Dunder names are
    excluded — they are never a meaningful cross-module import target.
    """
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    names.add(tgt.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return {n for n in names if not (n.startswith("__") and n.endswith("__"))}


def _top_level_bound_names(tree: ast.Module) -> set[str]:
    """Names *bound* at module top level: definitions plus imported aliases."""
    names = set(_top_level_defined_names(tree))
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module == "__future__":
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                names.add(alias.asname or alias.name)
    return names


def _split_dropped_api(current_content: str, target_content: str) -> list[str]:
    """Return top-level names present in the original target but no longer
    defined *or* re-exported in the proposed (shrunk) target.
    """
    try:
        old = ast.parse(current_content)
        new = ast.parse(target_content)
    except SyntaxError:
        return []
    required = _top_level_defined_names(old)
    exposed = _top_level_bound_names(new)
    return sorted(required - exposed)


def _is_self_build_doc_target(target: str) -> bool:
    return target.replace("\\", "/").strip().lower() == _SELF_BUILD_DOC_TARGET


def _missing_self_build_doc_topics(content: str) -> list[str]:
    low = (content or "").lower()
    missing: list[str] = []
    # Each topic's markers are ALTERNATIVES: any one satisfies the topic. This
    # matches how _SELF_BUILD_DOC_GUIDANCE phrases them ("or") and prevents a
    # Builder-following operator guide from being vetoed for not containing every
    # synonym (e.g. both :approval-list and :approval-triage, or a literal
    # "deny if").
    for label, markers in _SELF_BUILD_DOC_REQUIRED_TOPICS:
        if not any(marker in low for marker in markers):
            missing.append(label)
    return missing


def _split_target_too_large(content: str) -> tuple[bool, int]:
    """(too_large, line_count) for a would-be module split.

    The single-shot Builder cannot safely split a very large module — it keeps
    dropping public API and getting vetoed. Above :data:`_MAX_SPLIT_TARGET_LINES`
    the producer refuses the split up-front instead of burning a doomed
    generation. Pure; used only for ``split_module`` targets.
    """
    line_count = len((content or "").splitlines())
    return line_count > _MAX_SPLIT_TARGET_LINES, line_count


def _why_json_failed(build: dict[str, Any]) -> str:
    """Thin delegation — the wording lives in `core/builder_reply_diagnosis`."""
    return why_builder_reply_failed(build, max_tokens=_BUILDER_MAX_TOKENS)


def _is_critical(rel: str) -> bool:
    lower = rel.replace("\\", "/").strip().lower()
    for deny in CRITICAL_DENY:
        d = deny.lower()
        if d.endswith("/"):
            if lower.startswith(d):
                return True
        elif lower == d:
            return True
    return False


def _is_self_build_target_allowed(target: str) -> bool:
    """Provod #2 (bold-mode) acceptance policy for a grounded backlog target.

    Instead of the narrow hardcoded :data:`DEFAULT_CANDIDATE_TARGETS` trio, a
    grounded target is now accepted when it clears BOTH hard safety layers that
    already exist:

    * it is not a critical "organ" (:data:`CRITICAL_DENY` — main.py, loop.py,
      autonomous_runtime.py, safe_vcs.py, the self-apply/self-build machinery,
      config/, ...); and
    * the self-apply lane's own low-risk classifier would accept it
      (``core``/``cli``/``tools``/``tests`` ``*.py``, anything under ``docs/``,
      any ``*.md``; never ``.github``/``config``/``secrets``/lockfiles/keys).

    This only widens *candidate discovery* so the agent can act on its own
    architecture-audit findings. It does NOT widen apply authority: every
    produced patch is still dry-run, placed in the approval inbox for a human to
    approve, and applied only via the lane (which re-runs tests and auto-rolls
    back on red). Pure/deterministic; never raises.
    """
    rel = str(target or "").replace("\\", "/").strip()
    if not rel:
        return False
    # Canonicalize exactly the way the lane classifier does (drop ``.`` segments,
    # reject ``..``/absolute/drive paths) BEFORE the critical-organ check. Without
    # this, an alias like ``./core/loop.py`` or ``core/./loop.py`` slips past
    # _is_critical (raw-string match) yet classify_patch_risk normalizes it to the
    # critical ``core/loop.py`` and accepts it — admitting a critical organ the
    # fixed allowlist blocked. A path _normalize_rel rejects is never low-risk.
    canonical = _normalize_rel(rel)
    if canonical is None or _is_critical(canonical):
        return False
    ok, _reason, _rejected = classify_patch_risk(
        [FileChange(path=canonical, content="pass\n")]
    )
    return bool(ok)


def _default_file_reader(workspace: str | Path) -> Callable[[str], str | None]:
    root = Path(workspace).resolve()

    def read(path: str) -> str | None:
        target = (root / path).resolve()
        if root not in target.parents and target != root:
            return None
        try:
            return target.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError, UnicodeDecodeError):
            return None

    return read


def _llm_json_with_raw(
    llm: Any, *, system: str, user: str, max_tokens: int = 2000
) -> tuple[dict | None, str]:
    """Like :func:`_llm_json`, but also returns the RAW reply."""
    safe_user, _redact_meta = prepare_text_for_llm_boundary(user)
    try:
        answer = llm.complete(
            system=system, user=safe_user, max_tokens=max_tokens, temperature=0.0,
            allow_continuation=False,
        )
    except TypeError:
        # Wrappers and fakes that predate the flag, then positional-only ones.
        try:
            answer = llm.complete(
                system=system, user=safe_user, max_tokens=max_tokens, temperature=0.0
            )
        except TypeError:
            answer = llm.complete(system, safe_user)
    raw = answer if isinstance(answer, str) else ""
    return extract_json_object(raw), raw


def _llm_json(
    llm: Any, *, system: str, user: str, max_tokens: int = 2000
) -> dict | None:
    parsed, _raw = _llm_json_with_raw(
        llm, system=system, user=user, max_tokens=max_tokens
    )
    return parsed


def _preserve_rejected_raw(workspace: Path, roles: list[RoleOutput]) -> str | None:
    """Persist a discarded builder reply to disk; return its repo-relative
    path.
    """
    raw = ""
    for role in roles:
        if role.role != "builder":
            continue
        candidate = role.data.pop("raw_reply", None)
        if candidate:
            raw = str(candidate)   # last non-empty wins; ALL roles are stripped
    if not raw.strip():
        return None
    try:
        from core.redaction import redact_dlp_text

        safe_raw, _s, _p = redact_dlp_text(raw)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        rel = Path("logs") / "self_build_rejects" / f"reject_{stamp}.txt"
        out = workspace / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(safe_raw, encoding="utf-8", newline="")
        return rel.as_posix()
    except Exception as exc:  # noqa: BLE001 — retention is diagnostics, never a blocker
        print(f"[self-build] raw-reply preservation failed: {exc}", file=sys.stderr)
        return None


# ── roles ───────────────────────────────────────────────────────────────────


def _manager_select(
    llm: Any, candidate_targets: tuple[str, ...]
) -> RoleOutput:
    """Choose exactly one small low-risk target from the hardcoded allowlist."""
    listing = "\n".join(f"- {t}" for t in candidate_targets)
    system = (
        "You are the Manager of a self-build team. Choose exactly ONE small, "
        "low-risk file to improve from the provided allowlist. You may not pick "
        "anything outside the list. Reply with strict JSON only: "
        '{"target": "<path or null>", "diagnosis": "<one sentence>"}.'
    )
    user = f"Allowlisted candidate targets:\n{listing}\n\nPick one or null."
    parsed = _llm_json(llm, system=system, user=user) or {}
    target = parsed.get("target")
    diagnosis = str(parsed.get("diagnosis") or "").strip()
    if not isinstance(target, str) or not target.strip():
        return RoleOutput("manager", "no_target", "manager selected no target")
    target = target.strip()
    if target not in candidate_targets:
        return RoleOutput(
            "manager",
            "no_target",
            f"manager picked off-allowlist target {target!r}",
            {"rejected_target": target},
        )
    if _is_critical(target):
        return RoleOutput(
            "manager",
            "no_target",
            f"manager picked critical target {target!r}",
            {"rejected_target": target},
        )
    return RoleOutput(
        "manager",
        "selected",
        f"selected {target}",
        {"target": target, "diagnosis": diagnosis},
    )


def _manager_from_grounded(
    provider: Callable[[], Any],
    candidate_targets: tuple[str, ...],
    *,
    workspace: str | Path,
    named_targets: frozenset[str] = frozenset(),
) -> RoleOutput:
    """Manager variant (TD-036) that takes its target + diagnosis from a
    grounded backlog candidate instead of inventing one via the LLM.

    ``named_targets`` — предмет, НАЗВАННЫЙ вызывающим (цель кампании дошла до
    рук через `campaign_io._engineering_hands`). Живой прогон 2026-09-17 показал,
    что он сюда доезжал и не значил ничего: попадал в `effective_allowed`, то
    есть в РАЗРЕШЁННОЕ, а выбор всё равно делала верхушка бэклога — хартия
    просила `core/self_build_producer.py`, руки брали `core/smart_memory.py`.
    Названный предмет — задание, а не разрешение: если обоснованный кандидат не
    он, честный ответ «не нашёл по названному», а не работа над чужим файлом.
    Пустое множество (никто не называл) оставляет прежний ход бэклога.
    """
    try:
        candidate = provider()
    except Exception:  # noqa: BLE001 — a broken selector must never break producer
        candidate = None
    if candidate is None:
        return RoleOutput("manager", "no_target", "no grounded backlog candidate")

    # Provod #2: widen the effective allowlist beyond the hardcoded trio to any
    # grounded target that clears BOTH hard safety layers (critical-organ deny +
    # the self-apply lane's low-risk classifier). Apply authority is unchanged —
    # every produced patch still needs human approval and passing tests.
    candidate_target = str(getattr(candidate, "target_path", "") or "").replace(
        "\\", "/"
    ).strip()
    effective_allowed = set(candidate_targets)
    if candidate_target and _is_self_build_target_allowed(candidate_target):
        effective_allowed.add(candidate_target)

    mapping = map_backlog_candidate(
        candidate,
        workspace=workspace,
        allowed_targets=effective_allowed,
    )
    if not mapping.ok or mapping.candidate is None:
        rejected = str(getattr(candidate, "target_path", "") or "").strip()
        data = {
            "rejected_target": rejected,
            "mapping_decision": mapping.decision,
        }
        if mapping.mapping_rule:
            data["mapping_rule"] = mapping.mapping_rule
        return RoleOutput("manager", "no_target", mapping.reason, data)

    mapped_candidate = mapping.candidate
    target = mapped_candidate.target_path
    diagnosis = mapped_candidate.problem_quote
    evidence_ref = mapped_candidate.evidence_ref
    if not target:
        return RoleOutput("manager", "no_target", "grounded candidate has no target")
    if named_targets and target not in named_targets:
        asked = ", ".join(sorted(named_targets))
        return RoleOutput(
            "manager",
            "no_target",
            f"the goal named {asked}; the grounded backlog offered {target!r} "
            f"instead, and a named subject is an instruction, not a permission",
            {"rejected_target": target, "named_targets": sorted(named_targets)},
        )
    if mapping.decision != "mapped" and target not in effective_allowed:
        return RoleOutput(
            "manager",
            "no_target",
            f"grounded target {target!r} is off-allowlist",
            {"rejected_target": target},
        )
    if _is_critical(target):
        return RoleOutput(
            "manager",
            "no_target",
            f"grounded target {target!r} is critical",
            {"rejected_target": target},
        )
    return RoleOutput(
        "manager",
        "selected",
        f"selected {target} (grounded)",
        {
            "target": target,
            "diagnosis": diagnosis,
            "grounded": True,
            "evidence_ref": evidence_ref,
            "mapping_decision": mapping.decision,
            "mapping_rule": mapped_candidate.mapping_rule,
            "source_target_path": mapped_candidate.source_target_path,
            "proposed_change": mapped_candidate.proposed_change,
            "proof_of_value": mapped_candidate.proof_of_value,
            "expected_effect": mapped_candidate.expected_effect,
            "confidence": mapped_candidate.confidence,
        },
    )


def _grounded_candidate_actionable(candidate: Any, workspace: str | Path) -> bool:
    """True if the producer could actually act on this ranked candidate.

    The selection-time veto on oversized splits died 2026-08-28: it was the
    SECOND copy of the premise MIR-179 buried — written when no incremental
    splitter existed, while the produce-phase scale gate has long routed
    oversized targets to the deterministic splitter. Its live cost, measured
    on the first granted tick: no candidate was actionable, the fallback
    returned the #1 (critical) target, and the run ended no_grounded_target
    with four workable splits waiting right behind it (MIR-183).
    """
    target = str(getattr(candidate, "target_path", "") or "")
    if _is_self_build_target_allowed(target):
        return True
    try:
        mapping = map_backlog_candidate(
            candidate,
            workspace=workspace,
            allowed_targets=DEFAULT_CANDIDATE_TARGETS,
        )
    except Exception:  # noqa: BLE001 — a broken mapper must never break selection
        return False
    return bool(
        mapping.ok
        and mapping.candidate is not None
        and not _is_critical(mapping.candidate.target_path)
    )


def _candidate_concrete_targets(candidate: Any, workspace: str | Path) -> set[str]:
    """Best-effort set of concrete paths a backlog candidate resolves to."""
    out: set[str] = set()
    raw = str(getattr(candidate, "target_path", "") or "").replace("\\", "/").strip()
    if raw:
        out.add(raw)
    try:
        mapping = map_backlog_candidate(
            candidate,
            workspace=workspace,
            allowed_targets=DEFAULT_CANDIDATE_TARGETS,
        )
        if mapping.ok and mapping.candidate is not None:
            concrete = str(mapping.candidate.target_path or "").replace("\\", "/").strip()
            if concrete:
                out.add(concrete)
    except Exception:  # noqa: BLE001, S110 — a broken mapper must never break selection
        pass
    return out


def _default_grounded_selector(
    workspace: str | Path,
    *,
    exclude_targets: frozenset[str] = frozenset(),
    only_targets: frozenset[str] = frozenset(),
) -> Callable[[], Any]:
    """Build the DEFAULT grounded backlog selector for a workspace (TD-036
    follow-up).

    ``exclude_targets`` is a cooldown set of concrete paths that were just
    critic-vetoed: candidates resolving to one of them are skipped so the
    run advances to the NEXT grounded candidate instead of re-picking the
    same wall (which would only be vetoed again). An empty set (the default)
    is a no-op.

    ``only_targets`` — предмет, названный целью. Верхушка бэклога отвечает на
    вопрос «что вообще стоит чинить», а не на вопрос «почини вот это»; без
    отбора названный файл был бы отвергнут управляющим, даже если кандидат на
    него лежит в бэклоге строкой ниже. Пустое множество — прежний ход.

    It never calls an LLM, never touches the network/git, and is fully best-
    effort: any import/load failure yields a selector that returns ``None``,
    so the Manager refuses (``no_patch``) instead of silently falling back
    to the LLM manager.
    """
    def _select() -> Any:
        try:
            from core.backlog_selector import load_backlog, select_top

            reviews = None
            try:
                from core.value_review import ValueReviewLog

                reviews = ValueReviewLog.for_workspace(workspace).list()
            except Exception:  # noqa: BLE001 — reviews are an optional signal
                reviews = None
            candidates = load_backlog(workspace, value_reviews=reviews)
            # Cooldown (A): a target that was just critic-vetoed is temporarily
            # excluded so the run advances to the NEXT grounded candidate rather
            # than banging on the same wall. Matching is on the concrete path so
            # an abstract backlog id that maps to a vetoed file is also skipped.
            if exclude_targets:
                candidates = [
                    c
                    for c in candidates
                    if not (_candidate_concrete_targets(c, workspace) & exclude_targets)
                ]
            # Названный предмет сужает бэклог до себя: иначе кандидат на него,
            # лежащий ниже верхушки, до управляющего не доедет никогда.
            if only_targets:
                candidates = [
                    c
                    for c in candidates
                    if _candidate_concrete_targets(c, workspace) & only_targets
                ]
            # Provod #1 follow-up: candidates are ranked highest-first. Prefer the
            # top-ranked candidate the producer can actually act on — directly
            # low-risk OR mapper-resolvable to an allowed concrete file — so a
            # higher-ranked but non-actionable target (e.g. a missing
            # ``.github/workflows/ci.yml``) does not shadow an actionable one, yet
            # a mappable tech-debt item is NOT skipped for a lower-ranked audit
            # item. If nothing is actionable, fall back to the #1 (honest refusal).
            for candidate in candidates:
                if _grounded_candidate_actionable(candidate, workspace):
                    return candidate
            return select_top(candidates)
        except Exception:  # noqa: BLE001 — a broken backlog must never break producer
            return None

    return _select


def _researcher_gather(
    file_reader: Callable[[str], str | None], target: str, diagnosis: str
) -> RoleOutput:
    """Read current file content (read-only) and assemble evidence."""
    current = file_reader(target)
    exists = current is not None
    current = current or ""
    line_count = len(current.splitlines())
    evidence = [
        f"target={target}",
        f"exists={exists}",
        f"current_lines={line_count}",
    ]
    if diagnosis:
        evidence.append(f"diagnosis={diagnosis}")
    return RoleOutput(
        "researcher",
        "gathered",
        f"read {target} ({line_count} lines, exists={exists})",
        {"current_content": current, "exists": exists, "evidence": evidence},
    )


def _normalize_builder_files(
    parsed: dict[str, Any], target: str
) -> tuple[list[dict[str, str]], str]:
    """Normalise a builder LLM reply into a canonical ``files`` list.

    Accepts the legacy single-file ``{"content": ...}`` shape (becomes a
    one-element list) and the multi-file ``{"files": [{path, content}, ...]}``.
    """
    raw_files = parsed.get("files")
    files: list[dict[str, str]] = []
    if isinstance(raw_files, (list, tuple)) and raw_files:
        seen: set[str] = set()
        for entry in raw_files:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path")
            content = entry.get("content")
            if not isinstance(path, str) or not path.strip():
                continue
            if not isinstance(content, str):
                content = ""
            norm = path.replace("\\", "/").strip()
            if norm in seen:
                continue
            seen.add(norm)
            files.append({"path": norm, "content": content})
    else:
        content = parsed.get("content")
        if not isinstance(content, str):
            content = ""
        files.append({"path": target, "content": content})

    norm_target = target.replace("\\", "/").strip()
    primary = next(
        (f["content"] for f in files if f["path"] == norm_target),
        "",
    )
    return files, primary


def _builder_generate(
    llm: Any,
    target: str,
    current_content: str,
    diagnosis: str,
    *,
    split_mode: bool = False,
    lessons: list[str] | None = None,
    dep_context: str | None = None,
) -> RoleOutput:
    """Generate the FULL proposed file content (never a diff).

    When ``split_mode`` is True the Builder is asked to split the oversized
    target module into several smaller files: it must return the shrunk target
    plus one or more NEW sibling modules, each as complete file content. The
    single-file path (``split_mode=False``) is unchanged and byte-identical to
    the historical behaviour.
    """
    safe_content, inj = prepare_untrusted_text_for_llm(
        current_content or "",
        source_label=f"self_build:{target}",
    )
    if safe_content is None:
        return RoleOutput(
            "builder",
            "failed",
            "file content blocked by injection guard",
            {"injection": inj.to_log_payload(), "target": target},
        )
    if split_mode:
        system = (
            "You are the Builder. Split the oversized target Python module into "
            "several SMALLER, cohesive files to reduce its size while preserving "
            "behaviour and its public API exactly. Return the COMPLETE content of "
            "the shrunk target file AND one or more NEW sibling modules (in the "
            "same package directory) that it imports from. Do NOT modify any other "
            "existing file. Every file must be the entire file content, never a "
            "diff. Reply with strict JSON only: "
            '{"files": [{"path": "<repo-relative path>", "content": "<full file>"}], '
            '"test_paths": ["tests"], '
            '"test_pattern": "<optional pytest -k pattern or null>", '
            '"reason": "<one sentence>", "confidence": <0..1>}. '
            "Exactly one file path must equal the target; the rest must be new "
            "modules. Keep every file well under the size limit. For any symbol "
            "you move out of the target, keep it importable FROM the target by "
            "re-exporting it (e.g. `from .<new_module> import <name>`), so existing "
            "importers and tests that do `from core.<target> import <name>` keep "
            "working. Do NOT modify knowledge/generated/AGENT_ANATOMY.md — the self-build head "
            "registers new core modules in that index automatically."
        )
    else:
        system = (
            "You are the Builder. Produce the COMPLETE new content of the target "
            "file — the entire file, not a diff or patch. Keep the change small and "
            "low-risk. Reply with strict JSON only: "
            '{"content": "<full file content>", "test_paths": ["tests"], '
            '"test_pattern": "<optional pytest -k pattern or null>", '
            '"reason": "<one sentence>", "confidence": <0..1>}.'
        )
    if _is_self_build_doc_target(target):
        system = f"{system}\n\n{_SELF_BUILD_DOC_GUIDANCE}"
    user = (
        f"Target file: {target}\n"
        f"Diagnosis: {diagnosis or '(none)'}\n\n"
        f"Current content:\n{safe_content or '(empty / new file)'}"
    )
    if dep_context:
        user = f"{dep_context.strip()}\n\n{user}"
    if lessons:
        lesson_block = "\n".join(
            f"- {str(item).strip()}" for item in lessons if str(item).strip()
        )
        if lesson_block:
            user = (
                "IMPORTANT — earlier attempts on this exact target FAILED. Do NOT "
                "repeat these mistakes. In particular, when splitting a module, "
                "move the DEFINITION of every symbol you reference (classes, "
                "dataclasses, functions) into a new module — never leave an import "
                "pointing to a name that no module actually defines. Past failures:\n"
                f"{lesson_block}\n\n"
            ) + user
    parsed, raw_reply = _llm_json_with_raw(
        llm, system=system, user=user, max_tokens=_BUILDER_MAX_TOKENS
    )
    if not parsed:
        return RoleOutput(
            "builder",
            "failed",
            "builder returned no parseable JSON",
            # MIR-071: the head preserves this to disk and strips it from the
            # report, so the misfire is diagnosable without ballooning logs.
            # `truncated` separates "the file does not fit in one pass" from
            # "the model wrote nonsense" — the same veto text used to cover both.
            {
                "raw_reply": raw_reply,
                "raw_chars": len(raw_reply),
                "truncated": bool(getattr(llm, "last_answer_was_truncated", False)),
            },
        )
    files, content = _normalize_builder_files(parsed, target)
    test_paths = parsed.get("test_paths") or ["tests"]
    if not isinstance(test_paths, (list, tuple)) or not test_paths:
        test_paths = ["tests"]
    test_pattern = parsed.get("test_pattern")
    if not isinstance(test_pattern, str) or not test_pattern.strip():
        test_pattern = None
    reason = str(parsed.get("reason") or diagnosis or "").strip()
    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    extra = len(files) - 1
    detail = f"generated {len(content)} chars (confidence {confidence:.2f})"
    if extra > 0:
        detail = (
            f"generated {len(content)} chars for target + {extra} new file(s) "
            f"(confidence {confidence:.2f})"
        )
    data: dict[str, Any] = {
        "content": content,
        "files": files,
        "test_paths": [str(p) for p in test_paths],
        "test_pattern": test_pattern,
        "reason": reason,
        "confidence": confidence,
    }
    if not (isinstance(content, str) and content.strip()):
        # MIR-071's live shape: a truncated reply fragment-parses into a dict
        # WITHOUT the content field (measured: 15948 tokens ≈ the 16000-token
        # builder cap → outer JSON unbalanced → an inner balanced fragment
        # "wins" extraction). The reply is not empty — say so, and carry the
        # raw so the head can preserve it.
        data["raw_reply"] = raw_reply
        data["raw_chars"] = len(raw_reply)
    return RoleOutput(
        "builder",
        "built" if content else "failed",
        detail,
        data,
    )


def _python_body_vetoes(label: str, body: str, *, report_parse: bool) -> list[str]:
    """Parse + attribute-sieve vetoes for one python body.

    The attribute subtype (meas_8fd5b4e5/meas_be27ac23): invented
    claim.state shipped twice past the kwargs sieve. Doubt = silence."""
    try:
        ast.parse(body)
    except SyntaxError as exc:
        return [f"{label} does not parse: {exc.msg}"] if report_parse else []
    from core.attribute_sieve import phantom_attribute_reason

    reason = phantom_attribute_reason(body)
    return [f"{label}: {reason}"] if reason else []


def _critic_review(
    target: str,
    current_content: str,
    build: dict[str, Any],
    *,
    confidence_threshold: float,
    imported_symbols: dict[str, list[str]] | None = None,
    required_symbols: dict[str, str] | None = None,
) -> RoleOutput:
    """Validate the built content; any failing check is a hard veto."""
    veto: list[str] = []
    content = build.get("content") or ""
    confidence = float(build.get("confidence") or 0.0)

    if not isinstance(content, str) or not content.strip():
        raw_chars = int(build.get("raw_chars") or 0)
        if raw_chars > 0:
            # MIR-071: the model DID reply (often a truncated JSON whose inner
            # fragment "won" extraction) — saying "empty" hid that for a
            # 15948-token, 84-cost-unit reply. Name the real failure.
            veto.append(
                f"builder reply did not parse into usable content "
                f"(raw_chars={raw_chars}; {_why_json_failed(build)})"
            )
        else:
            veto.append("empty generated content")
    if _is_critical(target):
        veto.append(f"target {target} is a critical file")
    if content and _looks_like_diff(content):
        veto.append("generated content looks like a diff, not full content")
    if content and content == current_content:
        veto.append("generated content is identical to current file")
    if content and target.lower().endswith(".py") and not _looks_like_diff(content):
        # The extra-files loop below deliberately skips the target.
        veto.extend(_python_body_vetoes(
            f"target {target}", content, report_parse=False))
    if len(content.encode("utf-8")) > _MAX_CONTENT_BYTES:
        veto.append(
            f"generated content exceeds {_MAX_CONTENT_BYTES} bytes"
        )
    if _is_self_build_doc_target(target):
        missing_topics = _missing_self_build_doc_topics(content)
        if missing_topics:
            veto.append(
                "docs/self_build.md is not a human-gated self-build operator "
                "guide; missing topics: " + ", ".join(missing_topics)
            )
    if not build.get("test_paths"):
        veto.append("no targeted tests specified")
    if confidence < confidence_threshold:
        veto.append(
            f"confidence {confidence:.2f} below threshold {confidence_threshold:.2f}"
        )

    files = build.get("files") or [{"path": target, "content": content}]
    norm_target = target.replace("\\", "/").strip()
    # Multi-file (split) guard: validate every EXTRA file the same way the target
    # is validated above. The target itself is already checked, so it is skipped
    # here — keeping the single-file path byte-identical (no extra files).
    for entry in files:
        path = str(entry.get("path") or "").replace("\\", "/").strip()
        if not path or path == norm_target:
            continue
        body = entry.get("content") or ""
        if not isinstance(body, str) or not body.strip():
            veto.append(f"new file {path} has empty content")
        if _is_critical(path):
            veto.append(f"file {path} is a critical file")
        if body and _looks_like_diff(body):
            veto.append(f"file {path} looks like a diff, not full content")
        if len(body.encode("utf-8")) > _MAX_CONTENT_BYTES:
            veto.append(f"file {path} exceeds {_MAX_CONTENT_BYTES} bytes")
        if path.lower().endswith(".py") and body and not _looks_like_diff(body):
            veto.extend(_python_body_vetoes(
                f"new file {path}", body, report_parse=True))

    ok, risk_reason, rejected = classify_patch_risk(
        [
            FileChange(path=str(f.get("path") or ""), content=f.get("content") or "")
            for f in files
        ]
    )
    if not ok:
        veto.append(f"risk classification failed: {risk_reason}")

    # Split-integrity guard: when the Builder splits a module into several files,
    # every top-level name that used to live in the target must remain importable
    # from it (defined or re-exported). Otherwise external importers/tests that do
    # `from core.<target> import <name>` break with ImportError — the exact churn
    # that kept rolling back the verifier split. Only runs on real splits (extra
    # files present), so the single-file path stays byte-identical.
    has_extra = any(
        str(e.get("path") or "").replace("\\", "/").strip() != norm_target
        for e in files
    )
    if (
        has_extra
        and target.lower().endswith(".py")
        and current_content
        and content
        and not _looks_like_diff(content)
    ):
        dropped = _split_dropped_api(current_content, content)
        if dropped:
            # Name the real importers first: a dropped symbol that another
            # project file actually imports is a guaranteed ImportError, so the
            # message points the Builder at the exact consumer files.
            hard_broken = [
                n for n in dropped if imported_symbols and n in imported_symbols
            ]
            for name in hard_broken[:4]:
                users = ", ".join(sorted(imported_symbols[name])[:3])
                veto.append(
                    f"split breaks a REAL import: {name} is imported by {users} "
                    f"but is no longer importable from {target}"
                )
            shown = ", ".join(dropped[:6])
            more = "" if len(dropped) <= 6 else f" (+{len(dropped) - 6} more)"
            veto.append(
                f"split drops top-level name(s) from {target} that other modules "
                f"or tests may import: {shown}{more}; keep them importable by "
                f"re-exporting from the target (e.g. `from .<new_module> import "
                f"<name>`)"
            )

    if target.lower().endswith(".py") and content and not _looks_like_diff(content):
        try:
            ast.parse(content)
        except SyntaxError as exc:
            veto.append(f"generated python does not parse: {exc.msg}")

    # HARD RULES from past rollbacks: every symbol that once caused a
    # rolled-back ImportError on this target must stay importable (defined or
    # re-exported) in ANY new content — split or plain rewrite. Deterministic:
    # the same rollback can never happen twice for the same symbol.
    if (
        required_symbols
        and target.lower().endswith(".py")
        and content
        and not _looks_like_diff(content)
    ):
        try:
            exposed = _top_level_bound_names(ast.parse(content))
        except SyntaxError:
            exposed = None
        if exposed is not None:
            for symbol in sorted(required_symbols):
                if symbol not in exposed:
                    veto.append(
                        f"HARD RULE violated ({required_symbols[symbol]}): "
                        f"symbol {symbol} must remain importable from {target} "
                        f"- a previous apply was rolled back for exactly this "
                        f"ImportError"
                    )

    decision = "veto" if veto else "pass"
    return RoleOutput(
        "critic",
        decision,
        "; ".join(veto) if veto else "all critic checks passed",
        {
            "veto_reasons": veto,
            "confidence": confidence,
            "risk_ok": ok,
            "rejected_files": rejected,
        },
    )


def _reporter_publish(
    inbox: Any,
    target: str,
    build: dict[str, Any],
    evidence: list[str],
    workspace: Any = None,
) -> RoleOutput:
    """Create exactly one approval inbox item for the TD-024 bridge."""
    content = build["content"]
    files = build.get("files") or [{"path": target, "content": content}]
    payload = build_self_apply_payload(
        files=[
            {"path": str(f.get("path") or ""), "content": f.get("content") or ""}
            for f in files
        ],
        reason=build.get("reason") or "",
        evidence=evidence,
        test_paths=build.get("test_paths") or ["tests"],
        test_pattern=build.get("test_pattern"),
        origin=PRODUCER_ORIGIN,
        rollback=DEFAULT_ROLLBACK,
        workspace=workspace,
    )
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
    dedup_key = f"self_apply:{target}:{digest}"
    extra = len(files) - 1
    from core.self_apply_lane import judge_touching_note

    summary = f"self-apply proposal for {target}"
    if extra > 0:
        summary = f"self-apply split proposal for {target} (+{extra} new file(s))"
    # MIR-139 (OWASP-уточнение оператора 2026-08-28): правка судей — не
    # только флаг, но и ЯВНО повышенный класс риска: обратим-то патч обратим,
    # ущерб — судебной системе, и машинные ворота ниже по течению обязаны
    # видеть это в поле risk, а не только глаз — в тексте.
    _note = judge_touching_note([f["path"] for f in files])
    summary = _note + summary
    _risk = "irreversible" if _note else "reversible"
    item = inbox.add(
        operation=SELF_APPLY_OPERATION,
        summary=summary,
        risk=_risk,
        reasons=tuple(evidence),
        payload=payload,
        dedup_key=dedup_key,
    )
    if getattr(item, "status", "pending") == "denied":
        # Block 3 (L10): the inbox remembered a denial of this same patch and
        # returned the denied item instead of filing it again.
        return RoleOutput(
            "reporter",
            "refused_repeat",
            f"identical to denied {item.id}: "
            f"{getattr(item, 'decision_reason', '') or '(no reason recorded)'}",
            {"approval_id": None, "dedup_key": dedup_key, "denied_id": item.id},
        )
    return RoleOutput(
        "reporter",
        "published",
        f"created approval item {item.id}",
        {"approval_id": item.id, "dedup_key": dedup_key},
    )


def _incremental_split_test_paths(workspace: str | Path, target: str) -> list[str]:
    """Targeted tests for a split step: importer tests from the dependency map,
    capped to the runner's argv limit, else the whole ``tests/`` suite."""
    test_paths: list[str] = ["tests"]
    try:
        from core.dependency_map import build_dependency_map
        from tools.run_tests import MAX_PATHS

        related = build_dependency_map(workspace, target).related_tests
        if related and len(related) <= MAX_PATHS:
            test_paths = sorted(related)
    except Exception:  # noqa: BLE001, S110 — dependency scan is advisory only
        pass
    return test_paths


def publish_incremental_split_step(
    *,
    inbox: Any,
    workspace: str | Path,
    step: Any,
    reason: str,
    reader: Callable[[str], str | None] | None = None,
) -> tuple[Any, list[str]]:
    """Publish ONE deterministic incremental-split step as a self-apply
    approval.
    """
    build: dict[str, Any] = {
        "files": [
            {"path": step.target, "content": step.target_content},
            {"path": step.new_module, "content": step.new_content},
        ],
    }
    # Keep knowledge/generated/AGENT_ANATOMY.md in sync (its drift check would fail otherwise).
    try:
        _sync_anatomy_index(
            build, step.target, reader or _default_file_reader(workspace),
            workspace=workspace,
        )
    except Exception:  # noqa: BLE001, S110 — doc sync is best-effort; lane catches drift
        pass
    try:
        _sync_anatomy_groups(build, step.target, reader or _default_file_reader(workspace))
    except Exception:  # noqa: BLE001, S110 — как выше: сторож анатомии поймает
        pass           # пропуск и откатит, молча уронить публикацию хуже
    test_paths = _incremental_split_test_paths(workspace, step.target)
    evidence = [
        f"mode={step.mode}",
        f"target={step.target}",
        f"new_module={step.new_module}",
        f"lines_moved={step.lines_moved}",
        f"moved_names={', '.join(step.moved_names)}",
        "generator=deterministic AST slice (no LLM)",
        *step.notes,
    ]
    payload = build_self_apply_payload(
        files=build["files"],
        reason=reason,
        evidence=evidence,
        test_paths=test_paths,
        origin=INCREMENTAL_SPLIT_ORIGIN,
        workspace=workspace,
    )
    digest = hashlib.sha256(step.target_content.encode("utf-8")).hexdigest()[:12]
    from core.self_apply_lane import judge_touching_note

    _split_paths = [f.get("path", "") for f in (payload.get("files") or [])]
    _split_note = judge_touching_note(_split_paths)
    item = inbox.add(
        operation=SELF_APPLY_OPERATION,
        summary=_split_note + (
            f"incremental split step for {step.target}: move "
            f"{len(step.moved_names)} name(s) into {step.new_module}"
        ),
        risk="irreversible" if _split_note else "reversible",
        reasons=tuple(evidence),
        payload=payload,
        dedup_key=f"self_split:{step.target}:{digest}",
    )
    return item, evidence


_SPLIT_TARGET_PREFIX = "split:"


def _concrete_split_target(raw: str) -> str | None:
    """Concrete ``core/x.py`` path behind a ``split:core/x.py`` backlog target,
    or ``None`` when ``raw`` is not a split target."""
    s = str(raw or "").replace("\\", "/").strip()
    if s.startswith(_SPLIT_TARGET_PREFIX):
        rest = s[len(_SPLIT_TARGET_PREFIX):].strip()
        return rest or None
    return None


def _deterministic_split_report(
    *,
    workspace: str | Path,
    concrete_target: str,
    inbox: Any,
    reader: Callable[[str], str | None],
    gates: list[str],
    roles: list[RoleOutput],
) -> ProducerReport | None:
    """Route an oversized-module split that the one-shot LLM path cannot handle to
    the deterministic incremental splitter and publish its step (identical to
    ``:self-split``).

    Returns a ``proposed`` :class:`ProducerReport` when the planner proves one
    safe extraction step, a ``no_patch`` report when it cannot — carrying either
    the planner's precise decline reason or, if the planner *raised*, the
    exception type/message (veto ``incremental_splitter_error``) so a crash is
    never disguised as a clean decline — or ``None`` only when the splitter module
    itself cannot be imported (capability genuinely absent) so the caller keeps
    its prior refusal. The Builder/Critic/LLM are never consulted on this path —
    the code is moved verbatim by AST slicing and the planner refuses any step it
    cannot prove safe.
    """
    try:
        from core.incremental_splitter import plan_incremental_split
    except Exception:  # noqa: BLE001 — splitter unavailable → keep prior behaviour
        return None
    try:
        plan = plan_incremental_split(workspace, concrete_target)
    except Exception as exc:  # noqa: BLE001 — a crashing planner must be surfaced, not disguised
        # The splitter IS present but threw on this input — that is a splitter
        # bug, NOT a genuine "no safe step" outcome. Returning None here would let
        # the caller fall back to its generic refusal ("no low-risk candidate" /
        # "too large for a safe single-pass split"), hiding a real crash. Surface
        # it as a distinct no_patch carrying the exception so the operator (and the
        # journaled report) can tell a crash from a clean decline. Nothing applied.
        roles.append(RoleOutput(
            "reporter",
            "error",
            f"incremental splitter crashed: {type(exc).__name__}",
            {"error_type": type(exc).__name__, "target": concrete_target},
        ))
        return ProducerReport(
            status="no_patch",
            reason=(
                f"incremental splitter crashed on {concrete_target}: "
                f"{type(exc).__name__}: {exc}"
            ),
            target_path=concrete_target,
            checked_gates=gates,
            role_outputs=roles,
            veto_reasons=["incremental_splitter_error"],
            next_human_action=(
                "The deterministic splitter raised an exception on this target — "
                "investigate it as a splitter bug, not a genuine 'no safe step'. "
                "Nothing was applied."
            ),
        )
    if plan.status != "planned" or plan.step is None:
        return ProducerReport(
            status="no_patch",
            reason=(
                f"incremental splitter could not plan a safe step for "
                f"{concrete_target}: {plan.reason}"
            ),
            target_path=concrete_target,
            checked_gates=gates,
            role_outputs=roles,
            next_human_action=(
                "No safe incremental split step this run — the deterministic "
                "planner only proposes steps it can prove safe."
            ),
        )
    step = plan.step
    item, _evidence = publish_incremental_split_step(
        inbox=inbox,
        workspace=workspace,
        step=step,
        reason=plan.reason,
        reader=reader,
    )
    if getattr(item, "status", "pending") == "denied":
        # Block 3 (L10): the same split step (same digest) was denied recently.
        roles.append(RoleOutput(
            "reporter", "refused_repeat",
            f"identical to denied {item.id}: "
            f"{getattr(item, 'decision_reason', '') or '(no reason recorded)'}",
            {"approval_id": None, "denied_id": item.id},
        ))
        return ProducerReport(
            status="denied_repeat", reason=roles[-1].detail,
            target_path=step.target, checked_gates=gates, role_outputs=roles,
            next_human_action="The same split step was denied recently; pick another step or file.",
        )
    roles.append(RoleOutput(
        "reporter",
        "published",
        f"created approval item {item.id}",
        {"approval_id": item.id, "generator": "incremental_splitter"},
    ))
    return ProducerReport(
        status="proposed",
        reason=plan.reason,
        target_path=step.target,
        approval_id=item.id,
        checked_gates=gates,
        role_outputs=roles,
        next_human_action=(
            f"Review approval {item.id}; approve then run :self-apply-run "
            "(deterministic AST split — targeted + full tests, auto-rollback)."
        ),
    )


# ── orchestration ───────────────────────────────────────────────────────────


def _has_pending_self_apply(inbox: Any) -> bool:
    for item in inbox.list():
        if getattr(item, "operation", "") != SELF_APPLY_OPERATION:
            continue
        if getattr(item, "status", "") in ("pending", "approved"):
            return True
    return False


def _waiting_self_apply_targets(inbox: Any) -> frozenset[str] | None:
    """Files under a pending/approved self-apply item. ``None`` = unknown (an
    item without a file list, or an inbox without the reader): the whole
    hand waits, as it did before block 3."""
    reader = getattr(inbox, "pending_targets", None)
    if reader is None:
        return None if _has_pending_self_apply(inbox) else frozenset()
    try:
        return reader(operation=SELF_APPLY_OPERATION)
    except Exception:  # noqa: BLE001 — an unreadable inbox keeps the old wait
        return None


def _denied_self_apply_targets(inbox: Any) -> frozenset[str]:
    """Files a human denied recently — the hand goes elsewhere (block 3, L10)."""
    reader = getattr(inbox, "recently_denied_targets", None)
    if reader is None:
        return frozenset()
    try:
        return frozenset(reader(operation=SELF_APPLY_OPERATION))
    except Exception:  # noqa: BLE001 — cooldown recall must never break producer
        return frozenset()


def produce_self_apply_proposal(
    *,
    workspace: str | Path,
    inbox: Any,
    llm: Any,
    vcs: Any = None,
    budget_snapshot: Any = None,
    kill_switch: Any = None,
    file_reader: Callable[[str], str | None] | None = None,
    candidate_targets: tuple[str, ...] | list[str] | None = None,
    confidence_threshold: float = _DEFAULT_CONFIDENCE_THRESHOLD,
    now_iso: str | None = None,
    registry: Any = None,
    grounded_selector: Callable[[], Any] | None = None,
    legacy_llm_manager: bool = False,
    lessons_provider: Callable[[str], list[str]] | None = None,
    recently_vetoed_targets: frozenset[str] | set[str] | tuple[str, ...] | None = None,
    max_builder_attempts: int = _DEFAULT_MAX_BUILDER_ATTEMPTS,
) -> ProducerReport:
    """Run the Manager/Researcher/Builder/Critic/Reporter pipeline to produce
    at most one validated low-risk self-apply proposal into the approval
    inbox.

    Returns a :class:`ProducerReport`. The function never applies the patch,
    never runs the lane, and creates at most one inbox item per call.
    """
    def _record(report: ProducerReport) -> ProducerReport:
        if registry is not None:
            try:
                registry.record_report(report)
            except Exception:  # noqa: BLE001, S110 — recording must never break producer
                pass
        return report

    targets = tuple(candidate_targets) if candidate_targets else DEFAULT_CANDIDATE_TARGETS
    gates: list[str] = []

    # ── gate 1: budget kill-switch (before ANY LLM-heavy work) ──────────────
    if kill_switch is not None and getattr(kill_switch, "active", False):
        return _record(ProducerReport(
            status="budget_kill_switch",
            reason=str(getattr(kill_switch, "reason", "") or "kill-switch active"),
            checked_gates=["kill_switch"],
            next_human_action="Resolve budget kill-switch before self-build.",
        ))
    gates.append("kill_switch")

    # ── gate 2: hour budget near-exhaustion ─────────────────────────────────
    if budget_snapshot is not None:
        headroom = hour_budget_headroom(budget_snapshot)
        near, reasons = is_budget_near_exhaustion(headroom)
        if near:
            return _record(ProducerReport(
                status="budget_wait",
                reason="; ".join(reasons) or "budget near exhaustion",
                checked_gates=[*gates, "budget"],
                next_human_action="Wait for the budget window to refill.",
            ))
    gates.append("budget")

    # ── gate 3: a self_apply approval on the SAME file is still waiting ─────
    # Block 3 (L9, 2026-09-03): waiting on one file is not exhaustion of the
    # hand. Only a candidate a waiting item already touches waits; a file a
    # human denied recently is on cooldown; when the producer chooses, both
    # sets are excluded so it advances to the next grounded candidate.
    waiting = _waiting_self_apply_targets(inbox)
    denied_cooldown = _denied_self_apply_targets(inbox)
    explicit = tuple(candidate_targets or ())
    if waiting is None or (explicit and set(explicit) & waiting):
        held = sorted(set(explicit) & waiting) if waiting is not None else []
        return _record(ProducerReport(
            status="approval_wait",
            reason=(
                f"a pending self_apply_lane.run approval already touches "
                f"{', '.join(held)}" if held else
                "a pending self_apply_lane.run approval item names no files"
            ),
            checked_gates=[*gates, "approval"],
            next_human_action="Resolve the existing self-apply approval first.",
        ))
    if explicit and set(explicit) <= denied_cooldown:
        return _record(ProducerReport(
            status="denied_cooldown",
            reason=f"a human denied a proposal for {', '.join(explicit)} recently",
            checked_gates=[*gates, "approval"],
            next_human_action="Revise against the denial reason or pick another file.",
        ))
    gates.append("approval")

    # ── gate 4: dirty working tree ──────────────────────────────────────────
    if vcs is not None and not vcs.is_clean():
        return _record(ProducerReport(
            status="dirty_tree_wait",
            reason="git working tree is not clean",
            checked_gates=[*gates, "dirty_tree"],
            next_human_action="Commit or stash local changes, then retry.",
        ))
    gates.append("dirty_tree")

    reader = file_reader or _default_file_reader(workspace)
    roles: list[RoleOutput] = []

    # ── Manager ─────────────────────────────────────────────────────────────
    # TD-036 follow-up: the grounded backlog selector is now the DEFAULT source
    # of the Manager's target + diagnosis, so the real callers
    # (``:self-build-produce`` and the daemon) stop inventing a diagnosis via the
    # LLM. Resolution order:
    #   * an explicit ``grounded_selector`` (tests / advanced callers), else
    #   * the default workspace-backed grounded selector.
    # There is NO LLM fallback: an empty grounded backlog yields ``no_patch``
    # rather than a fabricated diagnosis. The legacy LLM manager path runs ONLY
    # when explicitly opted into via ``legacy_llm_manager=True`` (debug/tests).
    if legacy_llm_manager:
        manager = _manager_select(llm, targets)
    else:
        named = frozenset(explicit)
        excluded = (frozenset(recently_vetoed_targets or ())
                    | (waiting or frozenset()) | denied_cooldown)
        selector = grounded_selector or _default_grounded_selector(
            workspace, exclude_targets=excluded, only_targets=named)
        manager = _manager_from_grounded(
            selector, targets, workspace=workspace, named_targets=named)
    roles.append(manager)
    if manager.decision != "selected":
        # A rejected grounded candidate is not evidence of a defect. In
        # particular, an abstract ``split:*`` signal may describe only an
        # architectural refactor. Do not turn that refusal into a deterministic
        # split proposal: publication requires a concrete grounded target that
        # the Manager actually selected.
        return _record(ProducerReport(
            status="no_patch" if legacy_llm_manager else "no_grounded_target",
            reason=manager.detail or "no low-risk candidate selected",
            checked_gates=gates,
            role_outputs=roles,
            next_human_action="No low-risk self-build candidate this run.",
        ))
    target = manager.data["target"]
    diagnosis = manager.data.get("diagnosis", "")
    split_mode = manager.data.get("mapping_rule") == "split_module"

    # Recall past FAILED attempts on this exact target so the Builder is warned
    # not to repeat a mistake already recorded in episodic memory (best-effort;
    # a recall failure must never change or break the producer).
    lessons: list[str] = []
    if lessons_provider is not None:
        try:
            lessons = list(lessons_provider(target) or [])
        except Exception:  # noqa: BLE001 — lesson recall must never break producer
            lessons = []

    # What this run READS, recorded at the moment of reading (MIR-121).
    read_sources = [f"file:{target}"]
    if lessons:
        read_sources.append("memory:self-build-lessons")

    # ── Researcher ──────────────────────────────────────────────────────────
    researcher = _researcher_gather(reader, target, diagnosis)
    roles.append(researcher)
    current_content = researcher.data["current_content"]
    evidence = list(researcher.data["evidence"])

    # ── Scale filter (C) → deterministic incremental split ──────────────────
    # A split of a very large module cannot be done safely in ONE Builder shot —
    # it keeps dropping public API and getting vetoed. Instead of refusing (which
    # dead-ended the run), hand it to the deterministic incremental splitter: it
    # moves one dependency-closed block verbatim by AST slicing (no LLM, no
    # token ceiling) and publishes one provably-safe extraction step for human
    # approval. Only if the splitter is unavailable/can't plan do we fall back to
    # the honest refusal below. Small splits (<= the one-shot budget) still take
    # the LLM Builder path unchanged.
    if split_mode:
        too_large, line_count = _split_target_too_large(current_content)
        if too_large:
            det = _deterministic_split_report(
                workspace=workspace,
                concrete_target=target,
                inbox=inbox,
                reader=reader,
                gates=gates,
                roles=roles,
            )
            if det is not None:
                return _record(det)
            return _record(ProducerReport(
                status="no_patch",
                reason=(
                    f"split target {target} has {line_count} lines "
                    f"(> {_MAX_SPLIT_TARGET_LINES}); too large for a safe "
                    "single-shot split — needs a human-scoped incremental split"
                ),
                target_path=target,
                checked_gates=gates,
                role_outputs=roles,
                next_human_action=(
                    "Scope an incremental split by hand (e.g. :self-split) — the "
                    "single-shot Builder cannot safely split a module this large."
                ),
            ))

    # ── Dependency map ───────────────────────────────────────────────────────
    # Before the Builder touches a Python module, measure its real consumers:
    # which project files import it, which symbols they take, and which test
    # files exercise it. The Builder gets this as an explicit contract, the
    # Critic cross-checks dropped names against REAL imports, and the importer
    # tests are appended to the targeted-test list. Best-effort: a scan failure
    # must never break the producer.
    dep_map = None
    dep_context: str | None = None
    if target.lower().endswith(".py"):
        try:
            from core.dependency_map import build_dependency_map

            dep_map = build_dependency_map(workspace, target)
            dep_context = dep_map.builder_context()
            evidence.extend(dep_map.summary_lines())
        except Exception:  # noqa: BLE001 — dep scan must never break the producer
            dep_map = None
            dep_context = None

    # ── Hard rules from past rollbacks ──────────────────────────────────────
    # Machine-extracted lessons (e.g. "ImportError: cannot import name 'X'")
    # persisted by the apply command. The Builder sees them as non-negotiable
    # constraints and the Critic enforces them deterministically.
    required_symbols: dict[str, str] = {}
    if target.lower().endswith(".py"):
        try:
            from core.self_build_rules import RuleStore, default_rules_path

            for rule in RuleStore(default_rules_path(workspace)).rules_for(target):
                if rule.kind == "keep_importable":
                    required_symbols[rule.symbol] = rule.source
            if required_symbols:
                evidence.append(
                    "hard_rules=" + ", ".join(sorted(required_symbols))
                )
                rules_block = (
                    "HARD RULES (learned from rolled-back applies; violating "
                    "any of these guarantees a veto): the following symbols "
                    "MUST remain importable from the target file: "
                    + ", ".join(sorted(required_symbols))
                )
                dep_context = (
                    f"{rules_block}\n\n{dep_context}" if dep_context else rules_block
                )
        except Exception:  # noqa: BLE001 — rules must never break the producer
            required_symbols = {}

    # ── Builder → Critic (with optional single retry on veto, B) ────────────
    # When ``max_builder_attempts > 1`` a Critic veto is retried ONCE with the
    # exact veto reasons fed back to the Builder as hard constraints, before the
    # run gives up. Two attempts total, never more. This NEVER applies anything —
    # a vetoed candidate is still blocked and no inbox item is created. Default
    # is a single attempt (byte-identical to the historical behaviour); the real
    # ``:self-build-produce`` command opts into two.
    max_attempts = max(1, int(max_builder_attempts))
    builder_lessons = list(lessons)
    attempts_used = 0
    builder = None
    critic = None
    for attempt in range(1, max_attempts + 1):
        attempts_used = attempt

        # ── Builder ─────────────────────────────────────────────────────────
        builder = _builder_generate(
            llm, target, current_content, diagnosis,
            split_mode=split_mode, lessons=builder_lessons, dep_context=dep_context,
        )
        roles.append(builder)

        # Importer test files are part of the change's blast radius: run them as
        # targeted tests so a contract break fails fast inside the lane.
        if builder.decision == "built" and dep_map is not None and dep_map.related_tests:
            try:
                paths = [str(p) for p in (builder.data.get("test_paths") or [])]
                for test_file in dep_map.related_tests[:10]:
                    if test_file not in paths:
                        paths.append(test_file)
                builder.data["test_paths"] = paths
            except Exception:  # noqa: BLE001, S110 — test enrichment must never break produce
                pass

        # Self-build head keeps the anatomy index in sync: a split that adds new
        # core/*.py modules must also register them in knowledge/generated/AGENT_ANATOMY.md, or
        # the repo's anatomy-sync test fails and the lane rolls the apply back.
        if builder.decision == "built":
            try:
                _sync_anatomy_index(builder.data, target, reader, workspace=workspace)
            except Exception:  # noqa: BLE001, S110 — doc sync must never break the producer
                pass

        # ── Critic ──────────────────────────────────────────────────────────
        critic = _critic_review(
            target,
            current_content,
            builder.data,
            confidence_threshold=confidence_threshold,
            imported_symbols=dep_map.imported_symbols if dep_map is not None else None,
            required_symbols=required_symbols or None,
        )
        roles.append(critic)
        if critic.decision != "veto":
            break

        veto_now = list(critic.data.get("veto_reasons", []))
        # MIR-071: before this veto is finalized (or retried), pull the raw
        # builder reply out of the role data and preserve it to disk — the
        # only diagnosable evidence of an expensive misfire.
        raw_path = _preserve_rejected_raw(Path(workspace), roles)
        if raw_path is not None:
            veto_now.append(f"raw builder reply preserved: {raw_path}")
        if attempt < max_attempts:
            # Feed the exact veto reasons back so the retry targets them directly
            # instead of blindly regenerating the same defective candidate.
            retry_note = (
                "A previous attempt on this exact target was REJECTED by the "
                "Critic for: "
                + ("; ".join(str(v) for v in veto_now) or "unspecified reasons")
                + ". Fix ALL of these and do not reintroduce them."
            )
            builder_lessons = [*list(lessons), retry_note]
            continue

        return _record(ProducerReport(
            status="critic_veto",
            sources=read_sources,
            reason=critic.detail,
            target_path=target,
            checked_gates=gates,
            role_outputs=roles,
            veto_reasons=veto_now,
            attempts=attempts_used,
            next_human_action="Critic vetoed the candidate; no approval created.",
        ))

    # ── Value gate (TD-035): reject no-effect changes before publishing ─────
    # Runs after the Critic's *technical* pass and before the Reporter creates
    # an approval item. A hard veto (no code effect) blocks publication and
    # creates NO inbox item; soft flags are attached to the evidence so a human
    # reviewer sees them, but never block. Doc (*.md) targets are exempt from
    # hard veto. Deterministic — no LLM/network/git side effects.
    value = evaluate_proposal_value(
        target,
        current_content,
        builder.data.get("content") or "",
        builder.data.get("reason") or "",
    )
    if value.vetoed:
        return _record(ProducerReport(
            status="value_veto",
            sources=read_sources,
            reason="; ".join(value.veto_reasons) or "no-effect change",
            target_path=target,
            checked_gates=gates,
            role_outputs=roles,
            veto_reasons=list(value.veto_reasons),
            value_flags=list(value.flags),
            next_human_action="Value gate vetoed a no-effect change; no approval created.",
        ))
    if value.flags:
        evidence.extend(f"value-flag: {flag}" for flag in value.flags)

    # ── Reporter ────────────────────────────────────────────────────────────
    reporter = _reporter_publish(inbox, target, builder.data, evidence, workspace)
    roles.append(reporter)
    if reporter.decision == "refused_repeat":
        return _record(ProducerReport(
            status="denied_repeat",
            sources=read_sources,
            reason=reporter.detail,
            target_path=target,
            checked_gates=gates,
            role_outputs=roles,
            attempts=attempts_used,
            next_human_action="The same patch was denied recently; revise it or pick another file.",
        ))
    approval_id = reporter.data["approval_id"]
    return _record(ProducerReport(
        status="proposed",
        sources=read_sources,
        reason=builder.data.get("reason") or diagnosis,
        target_path=target,
        approval_id=approval_id,
        checked_gates=gates,
        role_outputs=roles,
        value_flags=list(value.flags),
        attempts=attempts_used,
        next_human_action=(
            f"Review approval item {approval_id} and run "
            f":self-apply-run {approval_id} to apply it."
        ),
    ))

"""Deterministic mapper from abstract backlog items to concrete self-build targets.

The grounded backlog selector may surface a real but abstract debt item such as
``TD-011 / TD-012``. The self-build producer cannot safely hand that abstract
label to the Builder; it needs a concrete file target with local evidence. This
module is the narrow translation layer:

* known patterns only;
* project-owned local evidence only;
* no LLM, network, git, writes, approvals, scheduler, or runtime state access;
* missing/broken evidence returns ``unknown`` / ``no_target`` instead of raising.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from core.self_apply_lane import FileChange, classify_patch_risk


MODEL_DISCOVERY_TARGET = "core/model_discovery.py"
MODEL_DISCOVERY_TEST_TARGET = "tests/test_model_discovery.py"

# Oversized-module organ emits ``split:<rel>`` abstract targets. The prefix is
# duplicated as a module constant here so the mapper does not depend on the
# signal module (avoids an import cycle) while staying a single source of truth.
SPLIT_TARGET_PREFIX = "split:"

# Upper line budget for a single-pass (one model reply) module split. The Builder
# must emit the shrunk target plus all new sibling modules in one JSON reply,
# which is capped by the provider's output-token ceiling (~16k for the default
# model). NOTE: this is a proxy for "fits in one model reply", NOT a hard
# guarantee — files well above ~1500 lines will usually TRUNCATE in the Builder's
# reply and fail (safe rollback), because the token ceiling, not this number, is
# the real limit. Raised to 10000 to let the agent ATTEMPT larger modules; the
# true fix is still an incremental/multi-pass split lane.
SPLIT_ONE_SHOT_MAX_LINES = 10000

_MODEL_DISCOVERY_RULE = "td_011_012_model_discovery"
_MODEL_DISCOVERY_TITLES = frozenset(
    {
        "TD-011 - Live Model Discovery",
        "TD-012 - Provider Catalog Refresh",
        "TD-011 / TD-012 - Live Model Discovery + Provider Catalog Refresh (read-only)",
    }
)


@dataclass(frozen=True)
class MappedBacklogCandidate:
    """A concrete, evidence-backed candidate the producer can consume."""

    target_path: str
    signal_source: str
    evidence_ref: str
    problem_quote: str
    proposed_change: str
    proof_of_value: str
    expected_effect: str
    confidence: float
    source_target_path: str = ""
    mapping_rule: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_path": self.target_path,
            "signal_source": self.signal_source,
            "evidence_ref": self.evidence_ref,
            "problem_quote": self.problem_quote,
            "proposed_change": self.proposed_change,
            "proof_of_value": self.proof_of_value,
            "expected_effect": self.expected_effect,
            "confidence": self.confidence,
            "source_target_path": self.source_target_path,
            "mapping_rule": self.mapping_rule,
        }


@dataclass(frozen=True)
class TargetMappingResult:
    """Outcome of a deterministic target-mapping attempt."""

    decision: str  # concrete | mapped | no_target | unknown
    reason: str
    candidate: MappedBacklogCandidate | None = None
    target_path: str | None = None
    evidence_ref: str | None = None
    mapping_rule: str | None = None

    @property
    def ok(self) -> bool:
        return self.candidate is not None and self.decision in {"concrete", "mapped"}


@dataclass(frozen=True)
class _ReadResult:
    rel_path: str
    text: str
    status: str  # known | missing | broken


def map_backlog_candidate(
    candidate: Any,
    *,
    workspace: str | Path,
    allowed_targets: Iterable[str] = (),
) -> TargetMappingResult:
    """Map a grounded backlog candidate to a concrete self-build target.

    Already-concrete allowlisted candidates pass through as ``concrete``. Known
    abstract patterns may map to a small explicit target when local evidence
    proves the link. Everything else refuses.
    """
    if candidate is None:
        return TargetMappingResult("no_target", "no grounded backlog candidate")

    target = _field(candidate, "target_path")
    if not target:
        return TargetMappingResult("no_target", "candidate has no target_path")

    allowed = {str(t).replace("\\", "/").strip() for t in allowed_targets}
    if target in allowed:
        mapped = _copy_candidate(candidate, target_path=target, mapping_rule="concrete")
        return TargetMappingResult(
            "concrete",
            "candidate already has an allowlisted concrete target",
            mapped,
            target_path=target,
            evidence_ref=mapped.evidence_ref,
            mapping_rule="concrete",
        )

    if _is_model_discovery_candidate(candidate):
        return _map_model_discovery_candidate(candidate, workspace=workspace)

    if target.startswith(SPLIT_TARGET_PREFIX):
        return _map_split_candidate(candidate, target, workspace=workspace)

    return TargetMappingResult(
        "no_target",
        f"no deterministic mapper for abstract target {target!r}",
        target_path=target,
    )


def _map_split_candidate(
    candidate: Any, target: str, *, workspace: str | Path
) -> TargetMappingResult:
    """Map an oversized-module ``split:<rel>`` signal to its concrete .py file.

    A split target becomes *actionable* only when the underlying module really
    exists, is a low-risk editable ``.py`` file, and is not a critical/denied
    path. Critical modules (e.g. ``core/loop.py``) still resolve here so the
    ranking is honest, but the producer's Manager critical-gate blocks the
    actual edit — mirroring the report-only contract for the riskiest files.
    """
    rel = target[len(SPLIT_TARGET_PREFIX):].strip().replace("\\", "/")
    if not rel:
        return TargetMappingResult(
            "no_target", "split target has no module path", target_path=target
        )
    if not rel.endswith(".py"):
        return TargetMappingResult(
            "no_target",
            f"split target {rel!r} is not a Python module",
            target_path=target,
        )
    if not _patch_target_is_low_risk(rel):
        return TargetMappingResult(
            "no_target",
            f"split target {rel!r} is not a low-risk editable file",
            target_path=target,
        )
    read = _read_project_text(workspace, rel)
    if read.status != "known":
        return TargetMappingResult(
            "no_target",
            f"split target {rel!r} does not exist ({read.status})",
            target_path=target,
        )
    # One-shot feasibility guard: the Builder rewrites the shrunk target PLUS the
    # new sibling modules in a SINGLE model reply, which is bounded by the model's
    # output-token ceiling. A module far above this line budget cannot be split in
    # one pass (the reply truncates and parses to nothing), so it stays
    # report-only — like a critical file — until an incremental splitter exists.
    line_count = read.text.count("\n") + 1
    if line_count > SPLIT_ONE_SHOT_MAX_LINES:
        return TargetMappingResult(
            "no_target",
            (
                f"split target {rel!r} is too large for a single-pass split "
                f"({line_count} lines > {SPLIT_ONE_SHOT_MAX_LINES}); needs an "
                "incremental splitter"
            ),
            target_path=target,
        )
    mapped = _copy_candidate(candidate, target_path=rel, mapping_rule="split_module")
    return TargetMappingResult(
        "mapped",
        f"oversized module split resolved to concrete file {rel!r}",
        mapped,
        target_path=rel,
        evidence_ref=mapped.evidence_ref,
        mapping_rule="split_module",
    )


def _copy_candidate(
    candidate: Any,
    *,
    target_path: str,
    mapping_rule: str,
) -> MappedBacklogCandidate:
    return MappedBacklogCandidate(
        target_path=target_path,
        signal_source=_field(candidate, "signal_source"),
        evidence_ref=_field(candidate, "evidence_ref"),
        problem_quote=_field(candidate, "problem_quote"),
        proposed_change=_field(candidate, "proposed_change"),
        proof_of_value=_field(candidate, "proof_of_value"),
        expected_effect=_field(candidate, "expected_effect"),
        confidence=_confidence(candidate, default=0.6),
        source_target_path=_field(candidate, "target_path"),
        mapping_rule=mapping_rule,
    )


def _map_model_discovery_candidate(
    candidate: Any,
    *,
    workspace: str | Path,
) -> TargetMappingResult:
    if not _patch_target_is_low_risk(MODEL_DISCOVERY_TARGET):
        return TargetMappingResult(
            "no_target",
            f"mapped target {MODEL_DISCOVERY_TARGET!r} is not lane-allowlisted",
            target_path=MODEL_DISCOVERY_TARGET,
            mapping_rule=_MODEL_DISCOVERY_RULE,
        )

    tech_debt = _read_project_text(workspace, "TECH_DEBT.md")
    implementation = _read_project_text(workspace, MODEL_DISCOVERY_TARGET)
    tests = _read_project_text(workspace, MODEL_DISCOVERY_TEST_TARGET)
    for item in (tech_debt, implementation, tests):
        if item.status != "known":
            return TargetMappingResult(
                "unknown",
                f"{item.status}_evidence:{item.rel_path}",
                target_path=MODEL_DISCOVERY_TARGET,
                mapping_rule=_MODEL_DISCOVERY_RULE,
            )

    quote = _field(candidate, "problem_quote")
    evidence_ref = _field(candidate, "evidence_ref")
    if not evidence_ref.startswith("TECH_DEBT.md:") or quote not in tech_debt.text:
        return TargetMappingResult(
            "no_target",
            "candidate is not traceable to TECH_DEBT.md",
            target_path=_field(candidate, "target_path"),
            mapping_rule=_MODEL_DISCOVERY_RULE,
        )

    missing_impl = _missing_markers(
        implementation.text,
        (
            "Live Model Discovery + Provider Catalog diff",
            "read-only / dry-run (TD-011/012)",
            "build_discovery_audit",
            "build_discovery_report",
            "NEVER writes the catalog",
        ),
    )
    missing_tests = _missing_markers(
        tests.text,
        (
            "Tests for TD-011/012 read-only Live Model Discovery + catalog diff",
            "discovery never writes",
            "build_discovery_audit",
            "build_discovery_report",
            "no secret",
        ),
    )
    if missing_impl or missing_tests:
        missing = ", ".join(missing_impl + missing_tests)
        return TargetMappingResult(
            "no_target",
            f"missing_model_discovery_evidence:{missing}",
            target_path=MODEL_DISCOVERY_TARGET,
            mapping_rule=_MODEL_DISCOVERY_RULE,
        )

    mapped_refs = [
        evidence_ref,
        _line_ref(implementation.text, "Live Model Discovery + Provider Catalog diff", MODEL_DISCOVERY_TARGET),
        _line_ref(tests.text, "Tests for TD-011/012 read-only Live Model Discovery", MODEL_DISCOVERY_TEST_TARGET),
    ]
    mapped = MappedBacklogCandidate(
        target_path=MODEL_DISCOVERY_TARGET,
        signal_source=_field(candidate, "signal_source") or "tech_debt",
        evidence_ref="; ".join(ref for ref in mapped_refs if ref),
        problem_quote=quote,
        proposed_change=(
            "Scope a minimal read-only/dry-run improvement inside "
            "core/model_discovery.py; preserve no catalog write, no inference, "
            "and no secret-value output guarantees."
        ),
        proof_of_value=(
            "Update tests/test_model_discovery.py or an adjacent unit test to "
            "prove audit stays local, dry-run writes no catalog, and secrets do "
            "not leak."
        ),
        expected_effect=(
            "TD-011/TD-012 discovery foundation progresses without catalog "
            "write/apply, runtime model switching, or provider inference calls."
        ),
        confidence=max(_confidence(candidate, default=0.7), 0.75),
        source_target_path=_field(candidate, "target_path"),
        mapping_rule=_MODEL_DISCOVERY_RULE,
    )
    return TargetMappingResult(
        "mapped",
        "mapped TD-011/TD-012 abstract backlog target to core/model_discovery.py",
        mapped,
        target_path=mapped.target_path,
        evidence_ref=mapped.evidence_ref,
        mapping_rule=_MODEL_DISCOVERY_RULE,
    )


def _is_model_discovery_candidate(candidate: Any) -> bool:
    target = _normalize_title(_field(candidate, "target_path"))
    quote = _normalize_title(_field(candidate, "problem_quote"))
    source = _field(candidate, "signal_source")
    evidence_ref = _field(candidate, "evidence_ref")
    if source and source != "tech_debt":
        return False
    if evidence_ref and not evidence_ref.startswith("TECH_DEBT.md:"):
        return False
    return target in {"TD-011", "TD-012", "TD-011 / TD-012"} and quote in _MODEL_DISCOVERY_TITLES


def _normalize_title(text: str) -> str:
    text = (text or "").replace("—", "-")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _field(candidate: Any, name: str) -> str:
    return str(getattr(candidate, name, "") or "").replace("\\", "/").strip()


def _confidence(candidate: Any, *, default: float) -> float:
    try:
        value = float(getattr(candidate, "confidence", default))
    except (TypeError, ValueError):
        return default
    if value < 0:
        return default
    return min(value, 1.0)


def _read_project_text(workspace: str | Path, rel_path: str) -> _ReadResult:
    try:
        root = Path(workspace).resolve()
        target = (root / rel_path).resolve()
    except OSError:
        return _ReadResult(rel_path, "", "broken")
    if target != root and root not in target.parents:
        return _ReadResult(rel_path, "", "broken")
    try:
        return _ReadResult(rel_path, target.read_text(encoding="utf-8"), "known")
    except FileNotFoundError:
        return _ReadResult(rel_path, "", "missing")
    except (OSError, UnicodeDecodeError):
        return _ReadResult(rel_path, "", "broken")


def _missing_markers(text: str, markers: tuple[str, ...]) -> list[str]:
    return [marker for marker in markers if marker not in text]


def _line_ref(text: str, needle: str, rel_path: str) -> str:
    for index, line in enumerate(text.splitlines(), start=1):
        if needle in line:
            return f"{rel_path}:{index}"
    return rel_path


def _patch_target_is_low_risk(target_path: str) -> bool:
    ok, _reason, _rejected = classify_patch_risk(
        [FileChange(path=target_path, content="pass\n")]
    )
    return bool(ok)

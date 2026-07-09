"""Grounded backlog selector for the self-build producer (TD-036, Phase 1).

Turns the read-only signals from :mod:`core.backlog_signals` into ranked
:class:`BacklogCandidate`s and picks the top one. The whole point is
anti-hallucination: a candidate may originate ONLY from a registered signal,
must carry provenance (``evidence_ref``), and its ``problem_quote`` must be an
exact substring of the cited source. A candidate that fails provenance
validation is dropped; an empty backlog yields ``None`` (the producer then
refuses rather than inventing work).

Pure and deterministic: no LLM, no network, no git, no writes. ``load_backlog``
performs read-only file IO only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from core.backlog_signals import (
    ARCHITECTURE_AUDIT_SOURCE,
    CODE_TODO_SOURCE,
    OVERSIZED_MODULE_SOURCE,
    SignalRecord,
    ValuePenalties,
    anatomy_candidates,
    architecture_audit_candidates,
    code_todo_candidates,
    open_tech_debt,
    oversized_module_candidates,
    self_build_docs_candidate,
    value_review_penalties,
)

# Deterministic per-source base rank and confidence. Human-authored TECH_DEBT
# work outranks the docs pilot, which outranks the agent's own architecture-audit
# findings, which outrank the advisory anatomy follow-up list so TD-030...TD-036
# remain blocked unless explicitly mapped later.
_SOURCE_BASE_SCORE = {
    "tech_debt": 2.0,
    "self_build_docs": 1.5,
    ARCHITECTURE_AUDIT_SOURCE: 1.25,
    # The agent's own code self-inspection sits below human-authored debt, the
    # docs pilot, and the structural audit, but above the advisory anatomy list:
    # a concrete, line-anchored TODO is more actionable than an advisory heading.
    CODE_TODO_SOURCE: 1.1,
    "anatomy": 1.0,
    # Oversized-module advisories sit at the very bottom: they are report-only
    # (abstract ``split:`` target, no mapper) and never displace actionable work.
    OVERSIZED_MODULE_SOURCE: 0.9,
}
_SOURCE_CONFIDENCE = {
    "tech_debt": 0.7,
    "self_build_docs": 0.65,
    ARCHITECTURE_AUDIT_SOURCE: 0.55,
    CODE_TODO_SOURCE: 0.5,
    "anatomy": 0.5,
    OVERSIZED_MODULE_SOURCE: 0.4,
}
_PENALTY_SCORE = 1.0


@dataclass(frozen=True)
class BacklogCandidate:
    """One grounded, ranked backlog item. Every field is either copied verbatim
    from a source (``target_path``/``evidence_ref``/``problem_quote``) or a
    deterministic, non-fabricated template — never an invented claim."""

    target_path: str
    signal_source: str
    evidence_ref: str
    problem_quote: str
    proposed_change: str
    proof_of_value: str
    expected_effect: str
    confidence: float
    score: float = 0.0

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
            "score": self.score,
        }


def _traceable(quote: str, source_text: str) -> bool:
    """True iff the quote is a non-empty exact substring of the source text."""
    quote = (quote or "").strip()
    return bool(quote) and quote in (source_text or "")


def _finalize(
    records: list[SignalRecord],
    sources: Mapping[str, str],
    penalties: ValuePenalties | None = None,
) -> list[BacklogCandidate]:
    """Validate provenance, drop suppressed/untraceable records, enrich, and rank.

    ``sources`` maps ``signal_source`` -> the raw source text used to prove the
    quote. ``penalties`` suppresses/penalizes targets from human value reviews.
    """
    penalties = penalties or ValuePenalties.empty()
    candidates: list[BacklogCandidate] = []
    for rec in records:
        source_text = sources.get(rec.signal_source, "")
        if not _traceable(rec.problem_quote, source_text):
            continue  # provenance failed: quote not found in cited source
        if rec.target_path in penalties.suppressed:
            continue  # a human said this target was the wrong one
        base = _SOURCE_BASE_SCORE.get(rec.signal_source, 0.0)
        score = base - (_PENALTY_SCORE if rec.target_path in penalties.penalized else 0.0)
        score += getattr(rec, "rank_hint", 0.0)
        candidates.append(
            BacklogCandidate(
                target_path=rec.target_path,
                signal_source=rec.signal_source,
                evidence_ref=rec.evidence_ref,
                problem_quote=rec.problem_quote,
                proposed_change=(
                    f"Scope a minimal PR addressing this backlog item "
                    f"({rec.signal_source}: {rec.evidence_ref})."
                ),
                proof_of_value=(
                    "A new or updated test proving the change; full pytest green."
                ),
                expected_effect=(
                    f"Backlog item {rec.target_path} progressed and its source "
                    f"record updated."
                ),
                confidence=_SOURCE_CONFIDENCE.get(rec.signal_source, 0.0),
                score=score,
            )
        )
    # Deterministic order: highest score first, then stable by source then target.
    candidates.sort(key=lambda c: (-c.score, c.signal_source, c.target_path))
    return candidates


def build_backlog(
    *,
    tech_debt_text: str = "",
    anatomy_text: str = "",
    self_build_proposal_text: str = "",
    architecture_audit_records: list[SignalRecord] | None = None,
    architecture_audit_text: str = "",
    code_todo_records: list[SignalRecord] | None = None,
    code_todo_text: str = "",
    oversized_records: list[SignalRecord] | None = None,
    oversized_text: str = "",
    include_self_build_docs: bool = True,
    penalties: ValuePenalties | None = None,
) -> list[BacklogCandidate]:
    """Build the ranked backlog from already-loaded source texts. Pure."""
    records = (
        open_tech_debt(tech_debt_text)
        + (
            self_build_docs_candidate(self_build_proposal_text)
            if include_self_build_docs
            else []
        )
        + anatomy_candidates(anatomy_text)
        + list(architecture_audit_records or [])
        + list(code_todo_records or [])
        + list(oversized_records or [])
    )
    sources = {
        "tech_debt": tech_debt_text,
        "self_build_docs": self_build_proposal_text,
        "anatomy": anatomy_text,
        ARCHITECTURE_AUDIT_SOURCE: architecture_audit_text,
        CODE_TODO_SOURCE: code_todo_text,
        OVERSIZED_MODULE_SOURCE: oversized_text,
    }
    return _finalize(records, sources, penalties)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return True


def _load_architecture_audit(root: Path) -> tuple[list[SignalRecord], str]:
    """Run the read-only architecture audit and turn its priority gaps into
    grounded backlog signals. This is the wire that lets the agent find its own
    work from self-analysis, not only from human-authored docs.

    The audit only makes sense against a genuine checkout of this agent, so it is
    gated on a sentinel (``core/architecture_audit.py``). For any other workspace
    (e.g. a throwaway tmp dir in a unit test) it degrades to empty instead of
    reporting every missing repo file as a spurious gap.

    Best-effort: any import/audit failure degrades to an empty backlog so a bad
    audit never breaks self-build. The audit itself performs read-only file IO.
    """
    if not _path_exists(root / "core" / "architecture_audit.py"):
        return [], ""
    try:
        from core.architecture_audit import audit_architecture

        audit = audit_architecture(root)
        gaps = audit.to_dict().get("priority_gaps", [])
        return architecture_audit_candidates(
            gaps, exists=lambda rel: _path_exists(root / rel)
        )
    except Exception:
        return [], ""


# Directories the agent is allowed to edit and therefore worth self-inspecting.
# Kept intentionally to the low-risk Python homes; critical-organ and config
# files are filtered downstream by the producer's own gates, so scanning here is
# safe even if a marker happens to live in a file it may never actually change.
_CODE_TODO_SCAN_DIRS: tuple[str, ...] = ("core", "cli", "tools", "tests")


def _scan_code_todos(root: Path) -> tuple[list[SignalRecord], str]:
    """Read the agent's own editable ``*.py`` files and extract TODO/FIXME/XXX
    signals. Read-only, deterministic (sorted), and fully best-effort: an
    unreadable file degrades to empty rather than raising, so a bad file can
    never break backlog loading.
    """
    files: list[tuple[str, str]] = []
    for name in _CODE_TODO_SCAN_DIRS:
        base = root / name
        if not base.is_dir():
            continue
        try:
            paths = sorted(base.rglob("*.py"))
        except OSError:
            continue
        for path in paths:
            if "__pycache__" in path.parts:
                continue
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                continue
            files.append((rel, _read_text(path)))
    return code_todo_candidates(files)


# Directories scanned for oversized modules. Kept to the product code homes
# (not ``tests``): splitting large test files is not the modularity goal.
_OVERSIZED_SCAN_DIRS: tuple[str, ...] = ("core", "cli", "tools")


def _scan_oversized_modules(root: Path) -> tuple[list[SignalRecord], str]:
    """Read the agent's own ``*.py`` modules and flag oversized ones as
    report-only backlog advisories. Read-only, deterministic (sorted), and fully
    best-effort: an unreadable file degrades to empty rather than raising.
    """
    files: list[tuple[str, str]] = []
    for name in _OVERSIZED_SCAN_DIRS:
        base = root / name
        if not base.is_dir():
            continue
        try:
            paths = sorted(base.rglob("*.py"))
        except OSError:
            continue
        for path in paths:
            if "__pycache__" in path.parts:
                continue
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                continue
            files.append((rel, _read_text(path)))
    return oversized_module_candidates(files)


def load_backlog(
    workspace: str | Path,
    *,
    value_reviews: Any = None,
    item_target_map: Mapping[str, str] | None = None,
) -> list[BacklogCandidate]:
    """Read the Phase 1 sources from ``workspace`` (read-only) and build the
    ranked backlog. Missing files degrade to empty; never raises.

    ``value_reviews`` is an optional iterable of value-review records (e.g.
    ``ValueReviewLog.list()``); combined with ``item_target_map`` it yields the
    anti-repeat penalties. With neither, no penalties are applied.
    """
    root = Path(workspace)
    tech_debt_text = _read_text(root / "TECH_DEBT.md")
    anatomy_text = _read_text(root / "docs" / "AGENT_ANATOMY.md")
    self_build_proposal_text = _read_text(
        root / "docs" / "proposals" / "self-build-grounded-target-coverage-proposal.md"
    )
    include_self_build_docs = not _path_exists(root / "docs" / "self_build.md")
    audit_records, audit_text = _load_architecture_audit(root)
    code_todo_records, code_todo_text = _scan_code_todos(root)
    oversized_records, oversized_text = _scan_oversized_modules(root)
    penalties = None
    if value_reviews is not None:
        penalties = value_review_penalties(value_reviews, item_target_map)
    return build_backlog(
        tech_debt_text=tech_debt_text,
        anatomy_text=anatomy_text,
        self_build_proposal_text=self_build_proposal_text,
        architecture_audit_records=audit_records,
        architecture_audit_text=audit_text,
        code_todo_records=code_todo_records,
        code_todo_text=code_todo_text,
        oversized_records=oversized_records,
        oversized_text=oversized_text,
        include_self_build_docs=include_self_build_docs,
        penalties=penalties,
    )


def select_top(candidates: list[BacklogCandidate]) -> BacklogCandidate | None:
    """The single highest-ranked candidate, or ``None`` for an empty backlog."""
    return candidates[0] if candidates else None

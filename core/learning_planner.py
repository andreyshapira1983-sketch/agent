"""Learning planner: decides which local sources are worth feeding into controlled ingestion for a learning goal."""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from core.doc_routing import (
    CONFIDENCE_EVIDENCE_SOURCE_PATHS,
    DOCTRINE_CORPORATE_DOC_PATHS,
    is_confidence_evidence_diagnostic_question,
    is_doctrine_corporate_question,
)
from core.ingestion import DEFAULT_PROJECT_LIMIT, SKIP_DIR_NAMES, TEXT_EXTENSIONS
from core.ingestion_utils import _relative_label as _rel

if TYPE_CHECKING:
    from core.source_registry import SourceRecord


class _SourceLookup(Protocol):
    """The only requirement of the freshness window: an answer to `get_source`."""

    def get_source(self, source_id: str) -> SourceRecord | None: ...

# Files ingested within this window are considered fresh and get a score penalty.
_STALE_HOURS: float = 6.0
_DOCTRINE_CORPORATE_DOC_PRIORITY: dict[str, int] = {
    path.casefold(): len(DOCTRINE_CORPORATE_DOC_PATHS) - idx
    for idx, path in enumerate(DOCTRINE_CORPORATE_DOC_PATHS)
}
_CONFIDENCE_EVIDENCE_SOURCE_PRIORITY: dict[str, int] = {
    path.casefold(): len(CONFIDENCE_EVIDENCE_SOURCE_PATHS) - idx
    for idx, path in enumerate(CONFIDENCE_EVIDENCE_SOURCE_PATHS)
}


@dataclass(frozen=True)
class LearningPlan:
    goal: str
    root: str
    source_paths: tuple[str, ...]
    skipped_paths: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "root": self.root,
            "source_count": len(self.source_paths),
            "source_paths": list(self.source_paths),
            "skipped_count": len(self.skipped_paths),
            "skipped_paths": list(self.skipped_paths[:40]),
            "reasons": list(self.reasons),
        }

    def user_summary(self) -> str:
        preview = ", ".join(self.source_paths[:8])
        if len(self.source_paths) > 8:
            preview += ", ..."
        return f"(learning plan: sources={len(self.source_paths)}; {preview})"


class LearningPlanner:
    """Pick a bounded set of local sources for one learning pass."""

    def plan(
        self,
        *,
        workspace: Path,
        goal: str = "",
        root: str = ".",
        limit: int = DEFAULT_PROJECT_LIMIT,
        source_registry: _SourceLookup | None = None,
        stale_hours: float = _STALE_HOURS,
        evidence_query: str = "",
    ) -> LearningPlan:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        workspace = workspace.resolve()
        target = _resolve_inside_workspace(workspace, root)
        if not target.exists():
            raise FileNotFoundError(f"Path not found: {target}")
        # A weak spot with evidence searches its files with BM25 (core/weak_spot_retrieval.py).
        retrieved: frozenset[str] = frozenset()
        if evidence_query:
            from core.weak_spot_retrieval import retrieve_files

            retrieved = frozenset(r.casefold() for r in retrieve_files(workspace, evidence_query))
            if not retrieved and not _named_candidates(workspace, _named_paths(goal.casefold())):
                return LearningPlan(goal=goal, root=_rel(workspace, target), source_paths=(),
                                    reasons=("evidence of the weak spot matched no code file — nothing to study",))

        candidates = [target] if target.is_file() else list(_iter_candidates(target))
        scored: list[tuple[int, str, Path, list[str]]] = []
        skipped: list[str] = []
        goal_l = goal.casefold()
        named = _named_paths(goal_l)
        if named:
            candidates += _named_candidates(workspace, named)
        seen: set[Path] = set()
        read_last_read = _staleness_reader(source_registry)
        for path in candidates:
            try:
                path = path.resolve()
                path.relative_to(workspace)
            except ValueError:
                skipped.append(f"{path}: outside workspace")
                continue
            if not path.is_file():
                continue
            if path in seen:      # a named file may also be inside `root`
                continue
            seen.add(path)
            if path.suffix.casefold() not in TEXT_EXTENSIONS:
                skipped.append(f"{_rel(workspace, path)}: unsupported extension")
                continue
            score, reasons = _score(path, workspace=workspace, goal=goal_l, named=named)
            if retrieved and _rel(workspace, path).casefold() in retrieved:
                score += _RETRIEVED_BY_EVIDENCE_BONUS
                reasons.append("retrieved by the weak spot's evidence (BM25)")
            if score <= 0:
                skipped.append(f"{_rel(workspace, path)}: low learning value")
                continue
            rel_path = _rel(workspace, path)
            score = _apply_staleness(
                score, rel_path, last_read=read_last_read(rel_path),
                stale_hours=stale_hours,
            )
            scored.append((score, rel_path.casefold(), path, reasons))

        scored.sort(key=lambda item: (-item[0], item[1]))
        picked = scored[:limit]
        reasons = []
        for _rank_score, _key, path, item_reasons in picked[:8]:
            reasons.append(f"{_rel(workspace, path)}: {', '.join(item_reasons)}")
        return LearningPlan(
            goal=goal,
            root=_rel(workspace, target),
            source_paths=tuple(_rel(workspace, path) for _rank_score, _key, path, _reasons in picked),
            skipped_paths=tuple(skipped),
            reasons=tuple(reasons),
        )


def _resolve_inside_workspace(workspace: Path, raw: str) -> Path:
    candidate = Path(raw or ".")
    target = candidate.resolve() if candidate.is_absolute() else (workspace / candidate).resolve()
    try:
        target.relative_to(workspace)
    except ValueError as exc:
        raise PermissionError(f"Path escapes workspace: {target}") from exc
    return target


def _iter_candidates(root: Path):
    for path in root.rglob("*"):
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        if path.is_file():
            yield path


def _score(
    path: Path,
    *,
    workspace: Path,
    goal: str,
    named: frozenset[str] = frozenset(),
) -> tuple[int, list[str]]:
    rel = _rel(workspace, path).casefold()
    name = path.name.casefold()
    score = 0
    reasons: list[str] = []

    # A file named in the goal is re-ranked above generic bonuses; only candidates already inside the workspace are re-ranked,
    # since a path in the goal is untrusted text and must never widen what may be read.
    if named and _is_named(rel, named):
        score += _NAMED_BY_GOAL_BONUS
        reasons.append("named by the goal")
    doctrine_goal = is_doctrine_corporate_question(goal)
    confidence_goal = is_confidence_evidence_diagnostic_question(goal)

    if doctrine_goal and rel in _DOCTRINE_CORPORATE_DOC_PRIORITY:
        score += 300 + _DOCTRINE_CORPORATE_DOC_PRIORITY[rel]
        reasons.append("doctrine/corporate source")
    if confidence_goal and rel in _CONFIDENCE_EVIDENCE_SOURCE_PRIORITY:
        score += 360 + _CONFIDENCE_EVIDENCE_SOURCE_PRIORITY[rel]
        reasons.append("confidence/evidence verifier source")

    if name == "readme.md":
        score += 100
        reasons.append("project overview")
    if "архитектура" in name or "architecture" in name:
        score += 95
        reasons.append("architecture source")
    if rel.startswith("core/"):
        score += 70
        reasons.append("agent core")
    if rel.startswith("tools/"):
        score += 55
        reasons.append("tool contract")
    if rel.startswith("runtime/"):
        score += 50
        reasons.append("runtime")
    if rel.startswith("tests/"):
        score += 40
        reasons.append("verified behavior")

    focus_terms = _goal_terms(goal)
    if focus_terms and any(term in rel for term in focus_terms):
        score += 50
        reasons.append("matches learning goal")
    if not focus_terms and rel in _DEFAULT_CORE_FILES:
        score += 45
        reasons.append("default learning spine")

    if name.startswith("test_") and focus_terms and any(term in rel for term in focus_terms):
        score += 20
        reasons.append("goal-specific tests")
    if confidence_goal and (name == "readme.md" or rel.startswith("tools/")):
        score -= 140
        reasons.append("not primary evidence for confidence internals")

    return score, reasons


#: Must outrank every generic bonus (doctrine 300+, confidence 360+): a file the goal names outright is the subject of the pass.
_NAMED_BY_GOAL_BONUS = 500
#: Above any generic bonus (README 100, core 70), below an explicitly named path.
_RETRIEVED_BY_EVIDENCE_BONUS = 200

#: The dot is load-bearing: it separates a path from an ordinary word; extensionless names would make every goal word a 'named file'.
_PATH_IN_GOAL_RE = re.compile(r"[\w][\w./\\-]*\.[A-Za-z0-9_]{1,20}")
_TEXT_SUFFIXES = frozenset(ext.lstrip(".") for ext in TEXT_EXTENSIONS)


def _named_paths(goal: str) -> frozenset[str]:
    """Paths the goal names outright, normalised to workspace-relative form; dotted module notation (`core.memory`) becomes a path."""
    out: set[str] = set()
    for raw in _PATH_IN_GOAL_RE.findall(goal or ""):
        token = raw.replace("\\", "/").strip("./").casefold()
        if not token:
            continue
        if "/" not in token and token.rsplit(".", 1)[-1] not in _TEXT_SUFFIXES:
            token = token.replace(".", "/")
        out.add(token)
    return frozenset(out)


def _is_named(rel: str, named: frozenset[str]) -> bool:
    """True when `rel` is one of the named paths or the file they mean (with or without suffix, by whole path segments)."""
    stem = rel.rsplit(".", 1)[0] if "." in rel.rsplit("/", 1)[-1] else rel
    return any(
        rel == n or rel.endswith("/" + n) or stem == n or stem.endswith("/" + n)
        for n in named
    )


def _named_candidates(workspace: Path, named: frozenset[str]) -> list[Path]:
    """Files the goal names, resolved regardless of the pass's `root`; only the named file crosses it and must still resolve inside the workspace."""
    found: list[Path] = []
    for name in named:
        for candidate in (name, *(f"{name}{ext}" for ext in TEXT_EXTENSIONS)):
            try:
                path = (workspace / candidate).resolve()
                path.relative_to(workspace)
            except (ValueError, OSError):
                continue
            if path.is_file():
                found.append(path)
    return found


def _goal_terms(goal: str) -> tuple[str, ...]:
    if not goal:
        return ()
    terms: list[str] = []
    if is_doctrine_corporate_question(goal):
        terms.extend([
            "corporate_model",
            "central_agent_governance",
            "agent_anatomy",
            "roadmap",
            "commands_map",
            "subagent",
            "autonomy",
            "governance",
        ])
    if any(x in goal for x in ("repair", "саморемонт", "почини", "ошиб")):
        terms.extend(["self_repair", "repair", "run_tests", "read_logs", "diff_file"])
    if any(x in goal for x in ("memory", "памят", "knowledge", "знан")):
        terms.extend(["memory", "knowledge", "source_registry", "ingestion"])
    if any(x in goal for x in ("role", "режим", "router")):
        terms.extend(["role", "router", "knowledge_use", "learning"])
    if any(x in goal for x in ("tool", "инструмент")):
        terms.extend(["tools", "tool", "shell_exec", "file_read", "web_fetch"])
    if is_confidence_evidence_diagnostic_question(goal):
        terms.extend([
            "verifier",
            "evidence_support",
            "confidence_vector",
            "low_confidence",
            "evidence",
            "citation",
            "source_registry",
            "ranker",
        ])
    elif any(x in goal for x in ("verifier", "вериф")):
        terms.extend(["verifier", "evidence", "source", "ranker"])
    elif any(x in goal for x in ("evidence", "source")):
        terms.extend(["evidence", "source", "ranker"])
    return tuple(dict.fromkeys(terms))


def _staleness_reader(
    source_registry: _SourceLookup | None,
) -> Callable[[str], str | None]:
    """One pass over the store per plan instead of a query per file."""
    if source_registry is None:
        return lambda _rel: None
    bulk = getattr(source_registry, "load_sources", None)
    if callable(bulk):
        try:
            records = bulk()
        except Exception:  # noqa: BLE001 — no batch support: ask per item
            records = None
        # Check the RETURNED value, not the name's presence: a stub answers any call and its empty result must not read as 'no fresh records'.
        if isinstance(records, list) and all(hasattr(r, "id") for r in records):
            index = {
                r.id: r.last_read_at
                for r in records
                if getattr(r, "last_read_at", None)
            }
            return lambda rel: index.get(f"file:{rel}")

    def _one(rel: str) -> str | None:
        record = source_registry.get_source(f"file:{rel}")
        return getattr(record, "last_read_at", None) if record is not None else None

    return _one


def _apply_staleness(
    score: int,
    rel_path: str,
    *,
    last_read: str | None,
    stale_hours: float,
) -> int:
    """Reduce score for files ingested recently; leave score unchanged if no registry."""
    if stale_hours <= 0 or not last_read:
        return score
    try:
        ts = datetime.fromisoformat(last_read.rstrip("Z")).replace(tzinfo=timezone.utc)
    except ValueError:
        return score
    age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
    if age_h < stale_hours:
        return max(1, score - 60)  # deprioritise, but keep eligible
    return score


_DEFAULT_CORE_FILES = frozenset({
    "core/loop.py",
    "core/planner.py",
    "core/policy.py",
    "core/memory_policy.py",
    "core/evidence.py",
    "core/source_ranker.py",
    "core/source_registry.py",
    "core/knowledge_pipeline.py",
    "core/ingestion.py",
    "core/verifier.py",
    "tests/test_integration.py",
    "tests/test_verifier.py",
    "tests/test_knowledge_pipeline.py",
})

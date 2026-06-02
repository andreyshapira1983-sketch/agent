"""Learning planner.

Controlled ingestion is the tool. The LearningPlanner decides which local
sources are worth feeding into that tool for a learning goal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.ingestion import DEFAULT_PROJECT_LIMIT, SKIP_DIR_NAMES, TEXT_EXTENSIONS


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
    ) -> LearningPlan:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        workspace = workspace.resolve()
        target = _resolve_inside_workspace(workspace, root)
        if not target.exists():
            raise FileNotFoundError(f"Path not found: {target}")

        candidates = [target] if target.is_file() else list(_iter_candidates(target))
        scored: list[tuple[int, str, Path, list[str]]] = []
        skipped: list[str] = []
        goal_l = goal.casefold()
        for path in candidates:
            try:
                path = path.resolve()
                path.relative_to(workspace)
            except ValueError:
                skipped.append(f"{path}: outside workspace")
                continue
            if not path.is_file():
                continue
            if path.suffix.casefold() not in TEXT_EXTENSIONS:
                skipped.append(f"{_rel(workspace, path)}: unsupported extension")
                continue
            score, reasons = _score(path, workspace=workspace, goal=goal_l)
            if score <= 0:
                skipped.append(f"{_rel(workspace, path)}: low learning value")
                continue
            scored.append((score, _rel(workspace, path).casefold(), path, reasons))

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


def _score(path: Path, *, workspace: Path, goal: str) -> tuple[int, list[str]]:
    rel = _rel(workspace, path).casefold()
    name = path.name.casefold()
    score = 0
    reasons: list[str] = []

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

    return score, reasons


def _goal_terms(goal: str) -> tuple[str, ...]:
    if not goal:
        return ()
    terms: list[str] = []
    if any(x in goal for x in ("repair", "саморемонт", "почини", "ошиб")):
        terms.extend(["self_repair", "repair", "run_tests", "read_logs", "diff_file"])
    if any(x in goal for x in ("memory", "памят", "knowledge", "знан")):
        terms.extend(["memory", "knowledge", "source_registry", "ingestion"])
    if any(x in goal for x in ("role", "режим", "router")):
        terms.extend(["role", "router", "knowledge_use", "learning"])
    if any(x in goal for x in ("tool", "инструмент")):
        terms.extend(["tools", "tool", "shell_exec", "file_read", "web_fetch"])
    if any(x in goal for x in ("verifier", "вериф", "evidence", "source")):
        terms.extend(["verifier", "evidence", "source", "ranker"])
    return tuple(dict.fromkeys(terms))


def _rel(workspace: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except Exception:
        return str(path)


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

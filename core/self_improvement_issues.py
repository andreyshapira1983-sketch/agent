"""Durable lifecycle registry for self-improvement failures."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from core.state_integrity import read_state_jsonl, rewrite_state_jsonl


IssueStatus = Literal["open", "verified", "resolved"]
DEFAULT_ISSUE_PATH = Path("data") / "self_improvement_issues.jsonl"
_DUPLICATE_MIXIN_KEY = "incremental_splitter:duplicate_mixin_base_class"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def failure_fingerprint(text: str) -> str:
    """Stable fingerprint for equivalent failure evidence."""
    lowered = str(text or "").casefold()
    if "duplicate base class" in lowered or ("duplicate" in lowered and "mixin" in lowered):
        key = _DUPLICATE_MIXIN_KEY
    else:
        key = re.sub(r"\b(?:ain|run|ep)_[a-f0-9]+\b", "<id>", lowered)
        key = re.sub(r"\b\d+\b", "#", key)
        key = " ".join(key.split())[:500]
    return "sii_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class SelfImprovementIssue:
    fingerprint: str
    title: str
    action: str
    status: IssueStatus
    first_seen: str
    last_seen: str
    evidence: tuple[str, ...]
    related_files: tuple[str, ...]
    related_error_text: str
    suggested_next_action: str

    def to_dict(self) -> dict:
        data = dict(self.__dict__)
        data["evidence"] = list(self.evidence)
        data["related_files"] = list(self.related_files)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "SelfImprovementIssue":
        status = str(data.get("status") or "open")
        if status not in {"open", "verified", "resolved"}:
            status = "open"
        return cls(
            fingerprint=str(data.get("fingerprint") or ""),
            title=str(data.get("title") or "Unresolved self-improvement failure"),
            action=str(data.get("action") or "improve_failure_to_idea_pipeline"),
            status=status,  # type: ignore[arg-type]
            first_seen=str(data.get("first_seen") or _now()),
            last_seen=str(data.get("last_seen") or _now()),
            evidence=tuple(str(x) for x in data.get("evidence") or ()),
            related_files=tuple(str(x) for x in data.get("related_files") or ()),
            related_error_text=str(data.get("related_error_text") or ""),
            suggested_next_action=str(data.get("suggested_next_action") or ""),
        )


def issue_from_failure(text: str, observed_at: str) -> SelfImprovementIssue:
    lowered = text.casefold()
    duplicate_mixin = "duplicate base class" in lowered or (
        "duplicate" in lowered and "mixin" in lowered
    )
    if duplicate_mixin:
        title = "Fix the incremental splitter duplicate mixin base class issue"
        action = "repair_incremental_splitter_duplicate_mixin"
        files = ("core/incremental_splitter.py", "tests/test_incremental_splitter.py")
        suggested = "inspect core/incremental_splitter.py tests/test_incremental_splitter.py"
    else:
        title = "Turn the self-improvement failure into a bounded repair"
        action = "improve_failure_to_idea_pipeline"
        files = tuple(dict.fromkeys(re.findall(r"[A-Za-z0-9_./-]+\.py", text)))[:6]
        suggested = "review the issue evidence and propose one small read-only fix"
    return SelfImprovementIssue(
        fingerprint=failure_fingerprint(text),
        title=title,
        action=action,
        status="open",
        first_seen=observed_at,
        last_seen=observed_at,
        evidence=(text[:500],),
        related_files=files,
        related_error_text=text[:500],
        suggested_next_action=suggested,
    )


class SelfImprovementIssueRegistry:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def list(self) -> list[SelfImprovementIssue]:
        issues = [SelfImprovementIssue.from_dict(row) for row in read_state_jsonl(self.path)]
        return [issue for issue in issues if issue.fingerprint]

    def unresolved(self) -> list[SelfImprovementIssue]:
        return sorted(
            (issue for issue in self.list() if issue.status != "resolved"),
            key=lambda issue: issue.last_seen,
            reverse=True,
        )

    def upsert_failure(self, text: str, observed_at: str) -> SelfImprovementIssue:
        incoming = issue_from_failure(text, observed_at)
        issues = self.list()
        for index, current in enumerate(issues):
            if current.fingerprint != incoming.fingerprint:
                continue
            newer = _stamp(observed_at) > _stamp(current.last_seen)
            status: IssueStatus = "open" if newer and current.status == "resolved" else current.status
            merged = replace(
                current,
                status=status,
                last_seen=max((current.last_seen, observed_at), key=lambda value: _stamp(value)),
                evidence=tuple(dict.fromkeys((*current.evidence, *incoming.evidence)))[-8:],
                related_error_text=incoming.related_error_text or current.related_error_text,
            )
            issues[index] = merged
            self._save(issues)
            return merged
        issues.append(incoming)
        self._save(issues)
        return incoming

    def transition(
        self,
        *,
        status: IssueStatus,
        observed_at: str,
        fingerprint: str = "",
        action: str = "",
        evidence: str = "",
    ) -> SelfImprovementIssue | None:
        issues = self.list()
        for index, current in enumerate(issues):
            matches = (
                current.fingerprint == fingerprint
                if fingerprint
                else bool(action and current.action == action)
            )
            if not matches:
                continue
            if _stamp(observed_at) < _stamp(current.last_seen):
                return None
            extra = (evidence[:500],) if evidence else ()
            updated = replace(
                current,
                status=status,
                last_seen=observed_at,
                evidence=tuple(dict.fromkeys((*current.evidence, *extra)))[-8:],
            )
            issues[index] = updated
            self._save(issues)
            return updated
        return None

    def _save(self, issues: list[SelfImprovementIssue]) -> None:
        rewrite_state_jsonl(self.path, [issue.to_dict() for issue in issues])

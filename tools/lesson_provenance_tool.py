"""The provenance meter in the planner's hands (read-only)."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from tools.base import Tool


class LessonProvenanceTool(Tool):
    name = "lesson_provenance"
    description = (
        "Read the RECEIPT CHAIN for the agent's own lessons: derived_from -> "
        "injected -> acted -> measured, each link PROVEN only by an "
        "independent machine receipt (episodic record, delivery row, "
        "measurement record); a lesson's own prose caps at SELF_DECLARED. "
        "Verdict 'CAUSAL USE NOT PROVEN' with named missing links is a "
        "finished honest answer. Args: lesson_key (str, optional — omit to "
        "read every lesson in the store)."
    )
    risk = "read_only"

    def __init__(self, workspace_root: str = ".") -> None:
        self._workspace = workspace_root

    def run(self, *, lesson_key: str | None = None, **_kw: Any) -> dict[str, Any]:
        from core.causal_claim_store import load_claims
        from core.lesson_provenance import trace_lesson_provenance

        keys = (
            [lesson_key] if lesson_key
            else [extra["key"] for _c, extra in load_claims(self._workspace)]
        )
        reports = [
            trace_lesson_provenance(self._workspace, key) for key in keys
        ]
        rendered = (
            "\n\n".join(r.render() for r in reports)
            if reports else "no lessons in the claim store — nothing to trace"
        )
        return {
            "reports": [asdict(r) for r in reports],
            "rendered": rendered,
        }

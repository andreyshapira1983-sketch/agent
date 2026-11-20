"""Reflection engine — self-improvement feedback loop."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from core.ids import new_id
from core.learning_planner import LearningPlan, LearningPlanner
from core.llm import LLM
from core.models import MemoryRecord
from core.persistent_memory import PersistentMemoryStore
from core.weak_spot_retrieval import evidence_query
from core.workspace_reference import names_workspace_path


def learning_grounding(
    *, focus_areas: list[str], source_paths: list[str], workspace: Path | str
) -> dict[str, Any]:
    """Did the plan study the weak spots the diagnosis named, or other things?

    A name is resolved (a real path the plan studied), phantom (path-shaped but not a file) or unresolvable by shape (a topic).
    status: targeted | partial | substituted | invalid_diagnosis | unresolvable | none. Judges the RECORD, not the policy:
    a fallback may happen but may not be recorded as the thing it replaced.
    """
    root = Path(workspace)
    named = [f.strip() for f in focus_areas if f and f.strip()]
    if not named:
        return {"named": [], "resolved": [], "phantom": [],
                "unresolvable": [], "status": "none"}
    studied = set(source_paths or ())
    path_shaped = [f for f in named if _PATH_SHAPED_RE.search(f)]
    unresolvable = [f for f in named if f not in path_shaped]
    phantom = [f for f in path_shaped if not (root / f).is_file()]
    resolved = [f for f in path_shaped if f not in phantom and f in studied]
    if resolved and len(resolved) == len(named):
        status = "targeted"
    elif resolved:
        status = "partial"
    elif not path_shaped:
        status = "unresolvable"
    elif len(phantom) == len(path_shaped):
        status = "invalid_diagnosis"
    else:
        status = "substituted"
    return {
        "named": named, "resolved": resolved, "phantom": phantom,
        "unresolvable": unresolvable, "status": status,
    }



@dataclass(frozen=True)
class ReflectionConfig:
    """Tuning knobs for one reflection pass."""

    max_logs: int = 20
    min_occurrences: int = 2
    max_lessons: int = 10
    learning_limit: int = 10



@dataclass
class ErrorPattern:
    """A recurring failure signal extracted from the agent's execution logs."""

    event_type: str
    # Non-empty when event_type is "tool_result_error" — the tool that failed.
    tool_name: str
    sample_message: str
    count: int
    trace_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "tool_name": self.tool_name,
            "sample_message": self.sample_message,
            "count": self.count,
            "trace_ids": self.trace_ids[:5],
        }


LessonAction = Literal["learn_more", "repair", "monitor"]


@dataclass
class Lesson:
    """A structured insight produced by the LLM from one or more error patterns."""

    id: str = field(default_factory=lambda: new_id("lesson"))
    insight: str = ""
    action: LessonAction = "monitor"
    focus_area: str = ""
    confidence: float = 0.5
    # The underlying pattern that triggered this lesson (may be None for global lessons).
    pattern: ErrorPattern | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "insight": self.insight,
            "action": self.action,
            "focus_area": self.focus_area,
            "confidence": self.confidence,
            "pattern": self.pattern.to_dict() if self.pattern else None,
        }



@dataclass
class ReflectionReport:
    """Full output of one ReflectionEngine.reflect() pass."""

    logs_scanned: int
    events_scanned: int
    patterns_found: list[ErrorPattern]
    lessons: list[Lesson]
    learning_plan: LearningPlan | None
    memory_records_saved: int
    warnings: list[str] = field(default_factory=list)
    #: Studied the diagnosed weak spots, or other things instead (see learning_grounding); a substitution may happen but may not be recorded as what it replaced.
    learning_grounding: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "logs_scanned": self.logs_scanned,
            "events_scanned": self.events_scanned,
            "patterns_found": [p.to_dict() for p in self.patterns_found],
            "lessons_count": len(self.lessons),
            "lessons": [lesson.to_dict() for lesson in self.lessons],
            "learning_plan": (
                self.learning_plan.to_log_payload() if self.learning_plan else None
            ),
            "memory_records_saved": self.memory_records_saved,
            "warnings": self.warnings,
            "learning_grounding": self.learning_grounding or None,
        }

    def user_summary(self) -> str:
        lines = [
            (f"(reflection logs={self.logs_scanned} events={self.events_scanned} "
            f"patterns={len(self.patterns_found)} lessons={len(self.lessons)} "
            f"saved={self.memory_records_saved})")
        ]
        for lesson in self.lessons:
            lines.append(f"  [{lesson.action}] {lesson.insight[:100]}")
        if self.learning_plan:
            lines.append(f"  learning_plan: {self.learning_plan.user_summary()}")
        for w in self.warnings:
            lines.append(f"  warning: {w}")
        return "\n".join(lines)



#: Looks like a repository address, not a topic name; only such names are checked against disk.
_PATH_SHAPED_RE = re.compile(r"[\w./\\-]+\.(?:py|md|json|jsonl|toml|yaml|yml)|/")


def _checked_focus_area(focus: str, action: str) -> tuple[str, str]:
    """A lesson may not point at a file that does not exist."""
    focus = focus.strip()
    if not focus or not _PATH_SHAPED_RE.search(focus):
        return focus, action
    if names_workspace_path(focus):
        return focus, action
    return "", ("monitor" if action == "repair" else action)


class ReflectionEngine:
    """Reads recent agent logs, extracts failure patterns, formulates lessons."""

    def __init__(
        self,
        *,
        workspace: Path | str,
        persistent_memory: PersistentMemoryStore,
        llm: LLM,
        log_dir: Path | str | None = None,
        logger: Any | None = None,
        freeze_writes: bool = False,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.persistent_memory = persistent_memory
        self.llm = llm
        self.log_dir = Path(log_dir) if log_dir else self.workspace / "logs"
        self.logger = logger
        # Operator brake: lessons are still synthesised and reported but NOT persisted, closing a side channel around the MemoryWritePolicy
        # freeze (AGENT_FREEZE_AUTO_MEMORY); wired by AutonomousRuntime._run_reflection; off by default.
        self.freeze_writes = freeze_writes


    def reflect(self, config: ReflectionConfig | None = None) -> ReflectionReport:
        """Run one reflection pass. Returns a full report. Never raises."""
        config = config or ReflectionConfig()
        self._log("reflection_start", {
            "log_dir": str(self.log_dir),
            "max_logs": config.max_logs,
            "min_occurrences": config.min_occurrences,
            "max_lessons": config.max_lessons,
        })

        warnings: list[str] = []

        events, logs_scanned = self._load_recent_logs(config, warnings)
        patterns = self._extract_patterns(events, config)

        if not patterns:
            report = ReflectionReport(
                logs_scanned=logs_scanned,
                events_scanned=len(events),
                patterns_found=[],
                lessons=[],
                learning_plan=None,
                memory_records_saved=0,
                warnings=warnings,
            )
            self._log("reflection_stop", report.to_dict())
            return report

        lessons = self._synthesize_lessons(patterns, config, warnings)
        saved = self._save_lessons(lessons)
        learning_plan = self._build_learning_plan(lessons, config, warnings)
        grounding = learning_grounding(
            focus_areas=[
                lesson.focus_area for lesson in lessons
                if lesson.action in ("learn_more", "repair") and lesson.focus_area
            ],
            source_paths=list(learning_plan.source_paths) if learning_plan else [],
            workspace=self.workspace,
        )

        report = ReflectionReport(
            logs_scanned=logs_scanned,
            events_scanned=len(events),
            patterns_found=patterns,
            lessons=lessons,
            learning_plan=learning_plan,
            memory_records_saved=saved,
            warnings=warnings,
            learning_grounding=grounding,
        )
        self._log("reflection_stop", report.to_dict())
        return report


    def _load_recent_logs(
        self,
        config: ReflectionConfig,
        warnings: list[str],
    ) -> tuple[list[dict[str, Any]], int]:
        """Read up to config.max_logs most-recent *.jsonl files.

        Returns (all_events, files_scanned).
        """
        if not self.log_dir.is_dir():
            warnings.append(f"log_dir not found: {self.log_dir}")
            return [], 0

        files = sorted(
            self.log_dir.glob("*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[: config.max_logs]

        all_events: list[dict[str, Any]] = []
        for fpath in files:
            try:
                text = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                warnings.append(f"cannot read {fpath.name}: {exc}")
                continue
            for raw in text.splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    all_events.append(json.loads(raw))
                except json.JSONDecodeError:
                    pass  # corrupted line — skip silently

        return all_events, len(files)


    def _extract_patterns(
        self,
        events: list[dict[str, Any]],
        config: ReflectionConfig,
    ) -> list[ErrorPattern]:
        """Bucket error events into (event_type, tool_name) counters: index tool_call_id to tool name first, then bucket by failure kind."""
        tc_to_tool: dict[str, str] = {}
        for ev in events:
            if ev.get("event") == "tool_call":
                payload = ev.get("payload") or {}
                tc_id = payload.get("id", "")
                tool_name = payload.get("tool_name", "")
                if tc_id and tool_name:
                    tc_to_tool[tc_id] = tool_name

        buckets: dict[tuple[str, str], dict[str, Any]] = {}

        for ev in events:
            event_type = ev.get("event", "")
            payload = ev.get("payload") or {}
            trace_id = ev.get("trace_id", "")

            key: tuple[str, str] | None = None
            sample = ""

            if event_type == "tool_result":
                if payload.get("status") == "error":
                    tc_id = payload.get("tool_call_id", "")
                    tool_name = tc_to_tool.get(tc_id, "unknown")
                    sample = str(payload.get("error") or "")[:200]
                    key = ("tool_result_error", tool_name)

            elif event_type in ("replan", "replan_exhausted"):
                sample = str(
                    payload.get("reason")
                    or payload.get("trigger")
                    or payload.get("why")
                    or ""
                )[:200]
                key = (event_type, "")

            elif event_type == "autonomous_task_result":
                if payload.get("status") == "failed":
                    task = payload.get("task") or {}
                    kind = str(task.get("kind") or "")
                    sample = str(payload.get("summary") or "")[:200]
                    key = ("autonomous_task_failed", kind)

            elif event_type == "error":
                code = str(payload.get("code") or "")
                sample = str(payload.get("message") or "")[:200]
                key = ("error", code)

            elif event_type == "causal_observation":
                # The agent's own detectors, each signal in its own bucket (one turn raises several; "mismatch nine times" differs from
                # "nine turns with some defect"); not only failures.
                for signal in payload.get("defect_signals") or ():
                    label = str(signal)
                    bucket = buckets.setdefault(
                        ("defect_signal", label),
                        {"count": 0, "sample": "", "trace_ids": []},
                    )
                    bucket["count"] += 1
                    if not bucket["sample"]:
                        bucket["sample"] = str(payload.get("observed_mismatch") or "")[:200]
                    if trace_id and trace_id not in bucket["trace_ids"]:
                        bucket["trace_ids"].append(trace_id)
                continue

            if key is None:
                continue

            if key not in buckets:
                buckets[key] = {"count": 0, "sample": "", "trace_ids": []}
            buckets[key]["count"] += 1
            if sample and not buckets[key]["sample"]:
                buckets[key]["sample"] = sample
            if trace_id and trace_id not in buckets[key]["trace_ids"]:
                buckets[key]["trace_ids"].append(trace_id)

        patterns: list[ErrorPattern] = []
        for (ev_label, tool_name), data in sorted(
            buckets.items(), key=lambda x: -x[1]["count"]
        ):
            if data["count"] >= config.min_occurrences:
                patterns.append(
                    ErrorPattern(
                        event_type=ev_label,
                        tool_name=tool_name,
                        sample_message=data["sample"],
                        count=data["count"],
                        trace_ids=list(data["trace_ids"]),
                    )
                )
        return patterns


    _SYSTEM_PROMPT = (
        "You are the self-reflection module of an autonomous AI agent. "
        "Your job is to analyze recurring failure patterns from the agent's "
        "own execution logs and produce actionable lessons that will help the "
        "agent improve in future cycles.\n\n"
        "Return ONLY a valid JSON array of lesson objects. Each object:\n"
        '  "insight"    : string — one sentence describing the weakness revealed\n'
        '  "action"     : one of "learn_more" | "repair" | "monitor"\n'
        '  "focus_area" : string — file or module to focus on '
        '(e.g. "tools/web_fetch.py", "core/memory.py")\n'
        '  "confidence" : number 0.0-1.0\n\n'
        "Action semantics:\n"
        '  "learn_more" = agent needs deeper knowledge about this area\n'
        '  "repair"     = likely a code bug or misconfiguration to fix\n'
        '  "monitor"    = keep watching; not severe enough for action yet\n\n'
        "Rules: be concise; one lesson per pattern; return [] when unsure; "
        "do NOT wrap in markdown fences."
    )

    def _synthesize_lessons(
        self,
        patterns: list[ErrorPattern],
        config: ReflectionConfig,
        warnings: list[str],
    ) -> list[Lesson]:
        patterns_json = json.dumps(
            [p.to_dict() for p in patterns],
            ensure_ascii=False,
            indent=2,
        )
        user_prompt = (
            f"Recurring failure patterns from the last {config.max_logs} agent runs "
            f"(only patterns with ≥{config.min_occurrences} occurrences):\n\n"
            f"{patterns_json}\n\nProduce lessons."
        )

        try:
            raw = self.llm.complete(
                system=self._SYSTEM_PROMPT,
                user=user_prompt,
                max_tokens=1024,
                temperature=0.3,
            )
        except Exception as exc:  # noqa: BLE001 — reason stated above
            # warnings is returned and printed with the result, so an empty lesson list arrives with the reason.
            warnings.append(f"LLM call failed: {type(exc).__name__}: {exc}")
            return []

        lessons = self._parse_lessons(raw, patterns, warnings)
        return lessons[: config.max_lessons]

    def _parse_lessons(
        self,
        raw: str,
        patterns: list[ErrorPattern],
        warnings: list[str],
    ) -> list[Lesson]:
        text = raw.strip()

        # Strip accidental markdown code fences (```json ... ``` or ``` ... ```)
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            text = text.removeprefix("json")
            if "```" in text:
                text = text[: text.index("```")]
            text = text.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            warnings.append(
                f"lessons JSON parse failed ({exc}); raw={raw[:200]!r}"
            )
            return []

        if not isinstance(data, list):
            warnings.append(
                "LLM returned a non-array JSON for lessons; expected JSON array"
            )
            return []

        lessons: list[Lesson] = []
        for idx, item in enumerate(data):
            if not isinstance(item, dict):
                continue
            action = item.get("action", "monitor")
            if action not in ("learn_more", "repair", "monitor"):
                action = "monitor"
            focus, action = _checked_focus_area(
                str(item.get("focus_area", ""))[:200], action,
            )
            lessons.append(
                Lesson(
                    insight=str(item.get("insight", ""))[:300],
                    action=action,  # type: ignore[arg-type]
                    focus_area=focus,
                    confidence=float(item.get("confidence") or 0.5),
                    pattern=patterns[idx] if idx < len(patterns) else None,
                )
            )
        return lessons


    def _save_lessons(self, lessons: list[Lesson]) -> int:
        """Persist lessons as episodic MemoryRecords. Returns count saved."""
        if not lessons:
            return 0
        if self.freeze_writes:
            # Operator brake active: do not grow persistent memory; lessons are still returned in the report.
            self._log("reflection_writes_frozen", {"lessons_withheld": len(lessons)})
            return 0
        # Reflection goes through the same write policy as every other agent-auto write (consent, secrets, near-duplicates);
        # each lesson is an observation of one day of logs.
        from core.memory_policy import MemoryWritePolicy

        policy = MemoryWritePolicy()
        try:
            existing = list(self.persistent_memory.load())
        except Exception:  # noqa: BLE001 — an unreadable store is «no duplicates known»
            existing = []

        def _text_view(rec: MemoryRecord) -> MemoryRecord:
            # A lesson record stores a dict; the duplicate check compares prose so it sees the insight, not the dict's punctuation.
            body = rec.content
            if isinstance(body, dict) and isinstance(body.get("insight"), str):
                return rec.model_copy(update={"content": body["insight"]})
            return rec

        seen = [_text_view(rec) for rec in existing]
        records: list[MemoryRecord] = []
        for lesson in lessons:
            tags = [
                "reflection",
                "lesson",
                lesson.action,
                lesson.focus_area or "general",
                # Russian synonyms so keyword retrieval works for RU queries
                "урок",
                "рефлексия",
                "[НАБЛЮДЕНИЕ, один день]",
                "insight",
            ]
            decision = policy.decide(
                lesson.insight, tags, source="agent-auto", owner="self",
                existing=seen,
            )
            if decision.decision != "save":
                self._log("reflection_lesson_refused", {
                    "lesson_id": lesson.id,
                    "reasons": list(getattr(decision, "reasons", ()) or ()),
                })
                continue
            record = MemoryRecord(
                type="episodic",
                content=lesson.to_dict(),
                tags=tags,
                owner="reflection_engine",
                source="agent-auto",
                importance=lesson.confidence,
            )
            records.append(record)
            seen.append(_text_view(record))
        if not records:
            return 0
        return self.persistent_memory.save_many(records)


    def _build_learning_plan(
        self,
        lessons: list[Lesson],
        config: ReflectionConfig,
        warnings: list[str],
    ) -> LearningPlan | None:
        """Return a LearningPlan focused on the weak spots found, or None."""
        # Only "learn_more" and "repair" actions warrant deeper ingestion, and only a weak spot that cites its evidence; the evidence
        # (the error pattern) also becomes the BM25 query for the files to study (core/weak_spot_retrieval.py).
        wanted = [
            lesson for lesson in lessons
            if lesson.action in ("learn_more", "repair") and lesson.focus_area
        ]
        grounded = [lesson for lesson in wanted if evidence_query(lesson)]
        if len(grounded) < len(wanted):
            warnings.append(
                "weak spots without evidence are not studied: "
                + ", ".join(lesson.focus_area for lesson in wanted if lesson not in grounded)
            )
        focus_areas = list(dict.fromkeys(lesson.focus_area for lesson in grounded))
        if not focus_areas:
            return None

        goal = "reflection: study weak areas — " + ", ".join(focus_areas)
        try:
            return LearningPlanner().plan(
                workspace=self.workspace,
                goal=goal,
                limit=config.learning_limit,
                evidence_query=" ".join(evidence_query(lesson) for lesson in grounded),
            )
        except Exception as exc:  # noqa: BLE001 — reason stated above
            # Same channel as the lesson synthesiser: None never travels without the sentence explaining it.
            warnings.append(f"LearningPlanner failed: {type(exc).__name__}: {exc}")
            return None


    def _log(self, event: str, payload: Any) -> None:
        if self.logger is not None:
            self.logger.log(event, payload)


# §3.x — register ReflectionEngine._SYSTEM_PROMPT with the global Prompt Registry
try:
    from core.prompt_registry import register_prompt as _rp
    _rp("reflection.system", ReflectionEngine._SYSTEM_PROMPT,
        module="core.reflection",
        description="System prompt for the self-reflection / lesson synthesis module")
except ImportError:  # pragma: no cover
    pass

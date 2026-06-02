"""Reflection engine — self-improvement feedback loop.

After each AutonomousRuntime pass the ReflectionEngine reads the recent
logs, extracts error/failure patterns, calls the LLM to formulate lessons,
persists those lessons as episodic MemoryRecords, and optionally generates
a LearningPlan to fill the knowledge gaps it found.

This closes the loop that was previously only possible by a human:

    AutonomousRuntime.run()
        ↓  (writes logs/)
    ReflectionEngine.reflect()
        ↓  reads logs/ → extracts patterns
        ↓  patterns → LLM → structured lessons
        ↓  lessons → PersistentMemoryStore (type="episodic")
        ↓  lessons → LearningPlan → KnowledgePipeline
    next cycle: agent acts on what it learned from its own mistakes
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from core.ids import new_id
from core.learning_planner import LearningPlan, LearningPlanner
from core.llm import LLM
from core.models import MemoryRecord
from core.persistent_memory import PersistentMemoryStore


# ── Configuration ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ReflectionConfig:
    """Tuning knobs for one reflection pass."""

    # How many most-recent *.jsonl log files to scan.
    max_logs: int = 20
    # Minimum occurrences of a pattern to surface it as actionable.
    min_occurrences: int = 2
    # Cap on lessons saved to persistent memory per pass.
    max_lessons: int = 10
    # Passed to LearningPlanner.plan() when a plan is generated.
    learning_limit: int = 10


# ── Internal data structures ──────────────────────────────────────────────────

@dataclass
class ErrorPattern:
    """A recurring failure signal extracted from the agent's execution logs."""

    # e.g. "tool_result_error", "replan_exhausted", "autonomous_task_failed", "error"
    event_type: str
    # Non-empty when event_type is "tool_result_error" — the tool that failed.
    tool_name: str
    # First sample error message seen for this bucket.
    sample_message: str
    # Total occurrences across all scanned log files.
    count: int
    # Trace IDs of runs where this pattern appeared (capped at 5 for brevity).
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
    # One-sentence description of what the pattern reveals.
    insight: str = ""
    # What the agent should do: study more / fix code / keep watching.
    action: LessonAction = "monitor"
    # The file or module to focus on (e.g. "tools/web_fetch.py").
    focus_area: str = ""
    # LLM-reported confidence that this lesson is meaningful.
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


# ── Report ────────────────────────────────────────────────────────────────────

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "logs_scanned": self.logs_scanned,
            "events_scanned": self.events_scanned,
            "patterns_found": [p.to_dict() for p in self.patterns_found],
            "lessons_count": len(self.lessons),
            "lessons": [l.to_dict() for l in self.lessons],
            "learning_plan": (
                self.learning_plan.to_log_payload() if self.learning_plan else None
            ),
            "memory_records_saved": self.memory_records_saved,
            "warnings": self.warnings,
        }

    def user_summary(self) -> str:
        lines = [
            f"(reflection logs={self.logs_scanned} events={self.events_scanned} "
            f"patterns={len(self.patterns_found)} lessons={len(self.lessons)} "
            f"saved={self.memory_records_saved})"
        ]
        for lesson in self.lessons:
            lines.append(f"  [{lesson.action}] {lesson.insight[:100]}")
        if self.learning_plan:
            lines.append(f"  learning_plan: {self.learning_plan.user_summary()}")
        for w in self.warnings:
            lines.append(f"  warning: {w}")
        return "\n".join(lines)


# ── Engine ────────────────────────────────────────────────────────────────────

class ReflectionEngine:
    """Reads recent agent logs, extracts failure patterns, formulates lessons.

    Typical usage after an AutonomousRuntime pass::

        engine = ReflectionEngine(
            workspace=Path("."),
            persistent_memory=agent.persistent_memory,
            llm=agent.llm,
            logger=agent.log,
        )
        report = engine.reflect()
        print(report.user_summary())
    """

    def __init__(
        self,
        *,
        workspace: Path | str,
        persistent_memory: PersistentMemoryStore,
        llm: LLM,
        log_dir: Path | str | None = None,
        logger: Any | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.persistent_memory = persistent_memory
        self.llm = llm
        self.log_dir = Path(log_dir) if log_dir else self.workspace / "logs"
        self.logger = logger

    # ── Public API ────────────────────────────────────────────────────────────

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

        report = ReflectionReport(
            logs_scanned=logs_scanned,
            events_scanned=len(events),
            patterns_found=patterns,
            lessons=lessons,
            learning_plan=learning_plan,
            memory_records_saved=saved,
            warnings=warnings,
        )
        self._log("reflection_stop", report.to_dict())
        return report

    # ── Step 1: load recent logs ──────────────────────────────────────────────

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

    # ── Step 2: extract failure patterns ─────────────────────────────────────

    def _extract_patterns(
        self,
        events: list[dict[str, Any]],
        config: ReflectionConfig,
    ) -> list[ErrorPattern]:
        """Bucket error events into (event_type, tool_name) counters.

        Two-pass approach:
          1. Index tool_call_id → tool_name so we can name tool failures.
          2. Walk events and bucket by failure kind.
        """
        # Pass 1: build lookup from tool_call id to tool name.
        tc_to_tool: dict[str, str] = {}
        for ev in events:
            if ev.get("event") == "tool_call":
                payload = ev.get("payload") or {}
                tc_id = payload.get("id", "")
                tool_name = payload.get("tool_name", "")
                if tc_id and tool_name:
                    tc_to_tool[tc_id] = tool_name

        # Pass 2: accumulate failure buckets.
        # key: (event_type_label, tool_name_or_empty)
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

    # ── Step 3: LLM synthesis ─────────────────────────────────────────────────

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
        except Exception as exc:
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
            if text.startswith("json"):
                text = text[4:]
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
            lessons.append(
                Lesson(
                    insight=str(item.get("insight", ""))[:300],
                    action=action,  # type: ignore[arg-type]
                    focus_area=str(item.get("focus_area", ""))[:200],
                    confidence=float(item.get("confidence") or 0.5),
                    pattern=patterns[idx] if idx < len(patterns) else None,
                )
            )
        return lessons

    # ── Step 4: save lessons to persistent memory ─────────────────────────────

    def _save_lessons(self, lessons: list[Lesson]) -> int:
        """Persist lessons as episodic MemoryRecords. Returns count saved."""
        if not lessons:
            return 0
        records = [
            MemoryRecord(
                type="episodic",
                content=lesson.to_dict(),
                tags=[
                    "reflection",
                    "lesson",
                    lesson.action,
                    lesson.focus_area or "general",
                    # Russian synonyms so keyword retrieval works for RU queries
                    "урок",
                    "рефлексия",
                ],
                owner="reflection_engine",
                importance=lesson.confidence,
            )
            for lesson in lessons
        ]
        return self.persistent_memory.save_many(records)

    # ── Step 5: generate a LearningPlan from the lessons ─────────────────────

    def _build_learning_plan(
        self,
        lessons: list[Lesson],
        config: ReflectionConfig,
        warnings: list[str],
    ) -> LearningPlan | None:
        """Return a LearningPlan focused on the weak spots found, or None."""
        # Only "learn_more" and "repair" actions warrant deeper ingestion.
        focus_areas = list(
            dict.fromkeys(
                l.focus_area
                for l in lessons
                if l.action in ("learn_more", "repair") and l.focus_area
            )
        )
        if not focus_areas:
            return None

        goal = "reflection: study weak areas — " + ", ".join(focus_areas)
        try:
            return LearningPlanner().plan(
                workspace=self.workspace,
                goal=goal,
                limit=config.learning_limit,
            )
        except Exception as exc:
            warnings.append(f"LearningPlanner failed: {type(exc).__name__}: {exc}")
            return None

    # ── Internal helpers ──────────────────────────────────────────────────────

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

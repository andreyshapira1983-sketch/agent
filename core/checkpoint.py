"""§3.5 Checkpoint / Resume — durable mid-run state.

A *checkpoint* is a single JSONL line written to
``logs/checkpoints_<trace_id>.jsonl`` every time the agent loop crosses a
named phase boundary.  On ``--resume <trace_id>`` the CLI loads the LAST
checkpoint for that trace and uses it to fast-forward the loop past already-
completed phases.

Design constraints
------------------
* **Zero new dependencies** — stdlib + project's own ``core.ids``.
* **Append-only** — we only write; reading uses the *last* record in the file
  so partial writes (e.g. Ctrl+C mid-serialisation) are automatically ignored.
* **Opaque payloads** — the ``data`` field is ``dict[str, Any]``; the loader
  does not validate it beyond type-checking.  The loop owns interpretation.
* **No sensitive data** — artifacts and tool outputs are stored as metadata
  only (label + tool name + char count).  Raw output never goes to disk here;
  that's the TraceLogger's job.

Phases saved
------------
``observe``   — trace_id, question, file_hint written right after Observation.
``plan``      — attempt number + list of step IDs (step tools and arg hashes).
``act``       — per-step outcome: label, tool, chars, status (done/failed).
``respond``   — final answer (text) + char count.
``paused``    — resumable budget stop metadata.

Recovery semantics
------------------
The ``CheckpointLoader`` replays only from the LAST ``respond`` checkpoint.
If no ``respond`` was saved (crash during act), the loader returns ``None`` so
the full cycle runs again — safer than partial-answer injection.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.ids import new_id


# ── public phase constants ──────────────────────────────────────────────────

PHASE_OBSERVE = "observe"
PHASE_PLAN    = "plan"
PHASE_ACT     = "act"
PHASE_RESPOND = "respond"
PHASE_PAUSED  = "paused"

_VALID_PHASES = {PHASE_OBSERVE, PHASE_PLAN, PHASE_ACT, PHASE_RESPOND, PHASE_PAUSED}


# ── data model ───────────────────────────────────────────────────────────────

@dataclass
class CheckpointRecord:
    """One line in the checkpoint file."""

    phase: str
    trace_id: str
    data: dict[str, Any]
    checkpoint_id: str = field(default_factory=lambda: new_id("cp"))
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "phase": self.phase,
            "trace_id": self.trace_id,
            "ts": self.ts,
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CheckpointRecord":
        return cls(
            phase=d["phase"],
            trace_id=d["trace_id"],
            data=d.get("data", {}),
            checkpoint_id=d.get("checkpoint_id", new_id("cp")),
            ts=d.get("ts", 0.0),
        )


@dataclass
class ResumeContext:
    """What the loop receives when --resume is used.

    ``answer`` is set only when the *last* checkpoint phase is ``respond``
    (i.e. the previous run completed synthesis).  In that case the loop can
    return the cached answer immediately without re-running the LLM.

    ``artifacts`` is a lightweight summary — label → {tool, chars} — rebuilt
    from ACT checkpoints so the synthesizer can reference what was collected.
    """

    trace_id: str
    last_phase: str
    question: str
    file_hint: str | None
    answer: str | None               # set only if respond checkpoint exists
    artifacts: dict[str, dict[str, Any]]   # label → {tool, chars}
    attempt: int                     # last plan attempt recorded
    paused: dict[str, Any] | None = None


# ── writer ───────────────────────────────────────────────────────────────────

class CheckpointWriter:
    """Appends checkpoint records to ``<log_dir>/checkpoints_<trace_id>.jsonl``."""

    # Allowed characters in a trace_id — prevents path traversal.
    _SAFE_TRACE_ID = re.compile(r"^[a-zA-Z0-9_\-]{1,128}$")

    def __init__(self, trace_id: str, log_dir: Path) -> None:
        if not trace_id:
            raise ValueError("trace_id must not be empty")
        if not self._SAFE_TRACE_ID.match(trace_id):
            raise ValueError(
                f"trace_id contains disallowed characters: {trace_id!r}. "
                "Only alphanumerics, underscores, and hyphens are permitted."
            )
        self._trace_id = trace_id
        log_dir.mkdir(parents=True, exist_ok=True)
        self._path = log_dir / f"checkpoints_{trace_id}.jsonl"

    @property
    def path(self) -> Path:
        return self._path

    def save(self, phase: str, data: dict[str, Any]) -> CheckpointRecord:
        """Write one checkpoint record.  Returns the record for inspection."""
        if phase not in _VALID_PHASES:
            raise ValueError(f"Unknown checkpoint phase: {phase!r}")
        rec = CheckpointRecord(phase=phase, trace_id=self._trace_id, data=data)
        line = json.dumps(rec.to_dict(), ensure_ascii=False, default=str)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return rec

    # Convenience helpers — typed wrappers so callers don't pass raw dicts.

    def save_observe(self, question: str, file_hint: str | None = None) -> CheckpointRecord:
        return self.save(PHASE_OBSERVE, {"question": question, "file_hint": file_hint})

    def save_plan(self, attempt: int, step_ids: list[str]) -> CheckpointRecord:
        return self.save(PHASE_PLAN, {"attempt": attempt, "step_ids": step_ids})

    def save_act(self, label: str, tool: str, chars: int, status: str) -> CheckpointRecord:
        return self.save(PHASE_ACT, {"label": label, "tool": tool, "chars": chars, "status": status})

    def save_respond(self, answer: str) -> CheckpointRecord:
        return self.save(PHASE_RESPOND, {"answer": answer, "chars": len(answer)})

    def save_paused(self, data: dict[str, Any]) -> CheckpointRecord:
        return self.save(PHASE_PAUSED, data)


# ── loader ───────────────────────────────────────────────────────────────────

class CheckpointLoader:
    """Reads the checkpoint file for a given ``trace_id`` and builds
    a :class:`ResumeContext` from the last complete set of records."""

    def __init__(self, log_dir: Path) -> None:
        self._log_dir = log_dir

    def _path_for(self, trace_id: str) -> Path:
        return self._log_dir / f"checkpoints_{trace_id}.jsonl"

    def exists(self, trace_id: str) -> bool:
        return self._path_for(trace_id).exists()

    def load(self, trace_id: str) -> ResumeContext | None:
        """Return a ``ResumeContext`` or ``None`` if no usable checkpoint exists.

        Failure modes that return ``None`` (safe fallback = full re-run):
        - file not found
        - no OBSERVE record (can't reconstruct question)
        - JSON decode error on any line (corrupted file)
        """
        path = self._path_for(trace_id)
        if not path.exists():
            return None

        records: list[CheckpointRecord] = []
        try:
            for raw in path.read_text(encoding="utf-8").splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                records.append(CheckpointRecord.from_dict(json.loads(raw)))
        except (json.JSONDecodeError, KeyError):
            return None

        if not records:
            return None

        # ── reconstruct ────────────────────────────────────────────────────

        # OBSERVE gives us question + file_hint.
        observe_rec = next(
            (r for r in records if r.phase == PHASE_OBSERVE), None
        )
        if observe_rec is None:
            return None

        question: str = observe_rec.data.get("question", "")
        file_hint: str | None = observe_rec.data.get("file_hint")

        # Last PLAN gives us the attempt number.
        plan_recs = [r for r in records if r.phase == PHASE_PLAN]
        attempt = plan_recs[-1].data.get("attempt", 1) if plan_recs else 1

        # ACT records: label → last status for that label.
        artifacts: dict[str, dict[str, Any]] = {}
        for r in records:
            if r.phase == PHASE_ACT:
                lbl = r.data.get("label", "")
                if lbl and r.data.get("status") == "done":
                    artifacts[lbl] = {
                        "tool": r.data.get("tool", ""),
                        "chars": r.data.get("chars", 0),
                    }

        # Last RESPOND gives us the cached answer (full cycle completed).
        respond_recs = [r for r in records if r.phase == PHASE_RESPOND]
        answer: str | None = None
        if respond_recs:
            answer = respond_recs[-1].data.get("answer")

        paused_recs = [r for r in records if r.phase == PHASE_PAUSED]
        paused = paused_recs[-1].data if paused_recs else None
        last_phase = records[-1].phase

        return ResumeContext(
            trace_id=trace_id,
            last_phase=last_phase,
            question=question,
            file_hint=file_hint,
            answer=answer,
            artifacts=artifacts,
            attempt=attempt,
            paused=paused,
        )

"""The exam driver's closing marker is the last event of a turn, after the answer.

The driver (scripts/exam_driver.py) ends a turn when the console shows
`procedural_memory_update`. A marker is only a better rule than «40 seconds
of silence» if it provably comes after synthesis and the response; a name
is not a proof (Кодекс, 2026-09-05). Pinned here on a real loop run: the
order is respond → memory_write → episodic_memory_write → …
→ procedural_memory_update, and procedural_memory_update is the LAST event
the run journals. The driver names exactly that event.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.memory import WorkingMemory
from core.planner import LLMPlanner
from core.policy import PolicyGate
from core.smart_memory import EpisodicMemoryStore, ProceduralMemoryStore
from tests.conftest import FakeLLM
from tools.base import ToolRegistry
from tools.file_read import FileReadTool

_ROOT = Path(__file__).resolve().parents[1]
PLAN = json.dumps({"reasoning": "read", "steps": [{"tool": "file_read", "arguments": {"path": "doc.txt"}, "rationale": "r"}]})
SYNTH = (
    "Conclusion: alpha is there. [file:doc.txt]\nFacts:\n- alpha [file:doc.txt]\n"
    "Sources:\n1. file:doc.txt - doc.txt\nConfidence: high\nUnverified: nothing\n"
)


def _events(log_dir: Path) -> list[str]:
    out: list[str] = []
    for path in sorted(log_dir.glob("trace_*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line).get("event", ""))
    return out


def _run_turn(tmp_path: Path, *, verbose: bool) -> None:
    (tmp_path / "doc.txt").write_text("alpha beta\n", encoding="utf-8")
    llm = FakeLLM(responses=[PLAN, SYNTH])
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=tmp_path))
    logger = TraceLogger(trace_id=new_trace_id(), log_dir=tmp_path / "logs", verbose=verbose)
    agent = AgentLoop(
        registry=registry, policy=PolicyGate(registry), llm=llm, logger=logger,
        planner=LLMPlanner(llm=llm, registry=registry), memory=WorkingMemory(),
        episodic_store=EpisodicMemoryStore(tmp_path / "ep.jsonl"),
        procedural_store=ProceduralMemoryStore(tmp_path / "pr.jsonl"),
    )
    agent.run(user_question="What is in doc.txt?", file_hint="doc.txt")


def test_procedural_memory_update_is_the_last_event_and_comes_after_respond(tmp_path: Path):
    _run_turn(tmp_path, verbose=False)

    events = _events(tmp_path / "logs")
    assert events[-1] == "procedural_memory_update", events[-5:]
    assert events.index("respond") < events.index("episodic_memory_write") < events.index("procedural_memory_update")


def test_the_drivers_wait_ends_on_the_loggers_console_after_the_answer(tmp_path: Path, capsys):
    """Fed the logger's real console lines, the driver's wait ends after respond and the episode.

    The driver reads the console, not the journal: a logger or marker change breaks it silently.
    """
    capsys.readouterr()
    _run_turn(tmp_path, verbose=True)
    console = capsys.readouterr().err.splitlines(keepends=True)
    printed = [line.split()[1] if len(line.split()) > 1 else "" for line in console]
    spec = importlib.util.spec_from_file_location("exam_driver", _ROOT / "scripts" / "exam_driver.py")
    assert spec and spec.loader
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    buf: list[str] = []
    now = [0.0]

    def sleep(seconds: float) -> None:  # one console line arrives per second of waiting
        now[0] += seconds
        if seconds and len(buf) < len(console):
            buf.append(console[len(buf)])

    why = driver.wait_for_turn_end(
        buf, 0, proc_alive=lambda: True, timeout=len(console) + 5, grace=0.0,
        sleep=sleep, clock=lambda: now[0],
    )

    assert why == "marker", f"the logger's console never showed a closing marker: {console[-3:]}"
    # «[PROC]» also tags procedure_feedback, printed one line before
    # procedural_memory_update, so the wait may end there; never before the answer.
    written = max(printed.index("respond"), printed.index("episodic_memory_write"))
    assert len(buf) > written, f"the turn ended before the answer and the episode, at {buf[-1]!r}"

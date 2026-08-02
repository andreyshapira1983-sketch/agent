"""S3 — premature completion as an unmet obligation, not a keyword match.

The detector this replaces asked whether the user's wording contained a
tool-ish word. Measured (`docs/audit/SENSOR_SIGNAL_MEASUREMENT.md`): **1 of 12**
phrasings that unambiguously demand a tool were recognised, and it fired on
*«объясни разницу между REST и GraphQL»* because `"разниц"` is a diff-tool
keyword.

The replacement asks whether some part of the cycle created a duty to observe
or to run something, and whether that duty was left unmet *without saying so*.

Three sources are wired (`intent`, `plan`, `freshness`). `acceptance_criteria`
is reported as unavailable and draws no conclusion — `not_wired` must never
behave like `no_requirement`.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from core.completion_obligation import (
    UNWIRED_SOURCES,
    evaluate_completion_obligations,
    paths_mentioned,
)
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.policy import PolicyGate
from tests.conftest import FakeLLM, FakePlanner
from tools.base import Tool, ToolRegistry, ToolResult


@dataclass
class _Step:
    tool: str
    status: str = "pending"
    action_spec: dict = field(default_factory=dict)

    def __post_init__(self):
        self.action_spec = {"tool_name": self.tool}


def _ev(**kw):
    kw.setdefault("question", "")
    kw.setdefault("answer", "")
    return evaluate_completion_obligations(**kw)


# ── the two measured defects of the old detector ─────────────────────────────

def test_a_conceptual_difference_question_is_not_an_obligation():
    """The false positive that motivated the replacement.

    «объясни разницу…» names no file, plans no tool and needs no fresh data, so
    no source creates a duty — regardless of the word «разницу».
    """
    res = _ev(question="объясни разницу между REST и GraphQL")

    assert res.required is False
    assert res.triggered is False
    assert res.requirement_sources == ()


def test_phrasings_the_keyword_list_missed_are_obligations_when_they_name_a_file():
    """Recall comes from the referenced object, not from the verb."""
    for question in (
        "сколько строк в core/loop.py",
        "что в файле core/loop.py",
        "core/loop.py — разбери его",
        "how big is core/loop.py",
    ):
        res = _ev(question=question)
        assert res.required is True, question
        assert "intent" in res.requirement_sources, question
        assert res.triggered is True, question


def test_a_named_file_that_was_actually_read_is_satisfied():
    res = _ev(
        question="что в файле core/loop.py",
        artifacts={"file:core/loop.py": {"tool": "file_read"}},
    )
    assert res.required is True
    assert res.satisfied is True
    assert res.triggered is False


# ── source: intent ───────────────────────────────────────────────────────────

def test_file_hint_alone_creates_the_obligation():
    res = _ev(question="перескажи", file_hint="docs/README.md")
    assert "intent" in res.requirement_sources
    assert res.triggered is True


def test_paths_mentioned_extracts_the_object():
    assert paths_mentioned("сколько строк в core/loop.py и tools/base.py") == (
        "core/loop.py", "tools/base.py",
    )
    assert paths_mentioned("объясни разницу между REST и GraphQL") == ()


# ── source: plan (admitted steps only) ───────────────────────────────────────

def test_an_admitted_observing_step_with_no_result_is_silently_missing():
    res = _ev(question="покажи логи", plan_steps=[_Step("read_logs")])
    assert res.required is True
    assert "plan" in res.requirement_sources
    assert res.missing_requirements == ("tool_execution",)
    assert res.triggered is True


def test_a_step_that_produced_an_artifact_is_satisfied():
    res = _ev(
        question="покажи логи",
        plan_steps=[_Step("read_logs", status="done")],
        artifacts={"log:latest": {"tool": "read_logs"}},
    )
    assert res.satisfied is True
    assert res.triggered is False


def test_a_failure_the_answer_reports_is_not_premature_completion():
    """An honest report of a failure is a different thing from a silent gap."""
    res = _ev(
        question="покажи логи",
        answer="Не удалось прочитать логи: read_logs вернул tool_error.",
        plan_steps=[_Step("read_logs", status="failed")],
        failure_codes=["tool_error"],
    )
    assert res.required is True
    assert res.triggered is False
    assert [o.status for o in res.obligations] == ["failed_but_reported"]


def test_the_same_failure_left_unmentioned_does_fire():
    res = _ev(
        question="покажи логи",
        answer="Вот сводка по логам: всё в порядке.",
        plan_steps=[_Step("read_logs", status="failed")],
        failure_codes=["tool_error"],
    )
    assert res.triggered is True
    assert [o.status for o in res.obligations] == ["silently_missing"]


def test_a_policy_block_the_answer_discloses_is_not_premature_completion():
    res = _ev(
        question="выполни команду",
        answer="Действие заблокировано политикой: shell_exec требует одобрения.",
        plan_steps=[_Step("shell_exec", status="failed")],
        denied_tools=["shell_exec"],
        failure_codes=["policy_blocked"],
    )
    assert res.triggered is False
    assert [o.status for o in res.obligations] == ["blocked_and_disclosed"]


def test_a_policy_block_kept_quiet_does_fire():
    res = _ev(
        question="выполни команду",
        answer="Готово.",
        plan_steps=[_Step("shell_exec", status="failed")],
        denied_tools=["shell_exec"],
        failure_codes=["policy_blocked"],
    )
    assert res.triggered is True


def test_a_non_observing_step_creates_no_obligation():
    """`file_write` is an action, not an observation owed back."""
    res = _ev(question="запиши файл", plan_steps=[_Step("file_write")])
    assert res.required is False


# ── source: freshness ────────────────────────────────────────────────────────

def test_realtime_without_evidence_is_an_obligation():
    res = _ev(question="какой сейчас курс", realtime_required=True)
    assert "freshness" in res.requirement_sources
    assert res.missing_requirements == ("fresh_evidence",)
    assert res.triggered is True


def test_realtime_with_evidence_is_satisfied():
    res = _ev(question="какой сейчас курс", realtime_required=True, chain_size=3)
    assert res.satisfied is True


def test_freshness_false_cannot_cancel_another_source():
    """Sources do not vote. One positive source is the duty."""
    res = _ev(
        question="что в файле core/loop.py",
        realtime_required=False,
    )
    assert res.required is True
    assert res.triggered is True
    assert res.requirement_sources == ("intent",)


def test_several_sources_are_all_reported():
    res = _ev(
        question="что сейчас в файле core/loop.py",
        plan_steps=[_Step("file_read")],
        realtime_required=True,
    )
    assert set(res.requirement_sources) == {"intent", "plan", "freshness"}
    assert set(res.missing_requirements) == {
        "workspace_observation", "tool_execution", "fresh_evidence",
    }


# ── acceptance_criteria: not_wired is not no_requirement ─────────────────────

def test_the_unwired_source_is_always_reported():
    quiet = _ev(question="объясни разницу между REST и GraphQL")
    busy = _ev(question="что в файле core/loop.py")

    for res in (quiet, busy):
        assert res.unavailable_sources == UNWIRED_SOURCES
        assert "acceptance_criteria" in res.unavailable_sources
        assert "acceptance_criteria" not in res.requirement_sources


def test_the_unwired_source_neither_creates_nor_cancels_a_duty():
    """It must not behave like a source that said "nothing is required"."""
    res = _ev(question="объясни разницу между REST и GraphQL")
    assert res.required is False          # it did not invent a duty …
    assert res.satisfied is False         # … and "no duty" is not "checked and passed"

    res2 = _ev(question="что в файле core/loop.py")
    assert res2.triggered is True         # … and it did not cancel a real one


def test_log_payload_keeps_unavailable_sources_separate():
    payload = _ev(question="привет").to_log_payload()
    assert payload["unavailable_sources"] == list(UNWIRED_SOURCES)
    assert payload["requirement_sources"] == []


# ── end to end, through the real loop ────────────────────────────────────────

class _FailingRead(Tool):
    name = "file_read"
    description = "read"
    side_effects = "read"

    @property
    def schema(self) -> dict:
        return {"type": "object", "properties": {"path": {"type": "string"}},
                "required": ["path"]}

    def run(self, **kwargs) -> ToolResult:
        return ToolResult(status="error", output=None, error="boom")


def _loop(workspace: Path, answer: str, sources=()):
    registry = ToolRegistry()
    registry.register(_FailingRead())
    loop = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=FakeLLM(responses=[answer] * 8),
        logger=TraceLogger(trace_id=new_trace_id(), log_dir=workspace / "logs",
                           verbose=False),
        planner=FakePlanner(sources=list(sources), reasoning="r"),
        max_replan_attempts=1,
    )
    events: list[tuple[str, dict]] = []
    _orig = loop.log.log
    loop.log.log = lambda et, *a, **k: (
        events.append((et, a[0] if a else {})), _orig(et, *a, **k))[1]
    return loop, events


def test_end_to_end_event_is_emitted_with_the_shadow_verdict(workspace: Path):
    loop, events = _loop(
        workspace,
        "Conclusion: ответ. [general-knowledge]\nConfidence: low",
    )

    loop.run("что в файле core/loop.py")

    payload = next((p for e, p in events if e == "completion_obligation"), None)
    assert payload is not None, "the obligation check must be journalled"
    assert payload["required"] is True
    assert payload["triggered"] is True
    assert payload["requirement_sources"] == ["intent"]
    assert payload["unavailable_sources"] == list(UNWIRED_SOURCES)
    # The detector it replaces is carried alongside, not deleted.
    assert "shadow_keyword_detector" in payload


def test_end_to_end_the_two_detectors_disagree_where_measured(workspace: Path):
    """The old detector stays quiet on a named file it has no keyword for."""
    loop, events = _loop(
        workspace,
        "Conclusion: ответ. [general-knowledge]\nConfidence: low",
    )

    loop.run("сколько строк в core/loop.py")

    payload = next(p for e, p in events if e == "completion_obligation")
    assert payload["triggered"] is True
    assert payload["shadow_keyword_detector"] is False, (
        "this is the recall gap the replacement exists to close"
    )


def test_end_to_end_an_ordinary_question_creates_no_obligation(workspace: Path):
    loop, events = _loop(
        workspace,
        "Conclusion: это разные подходы. [general-knowledge]\nConfidence: medium",
    )

    loop.run("объясни разницу между REST и GraphQL")

    payload = next(p for e, p in events if e == "completion_obligation")
    assert payload["required"] is False
    assert payload["triggered"] is False


# The regex `paths_mentioned` used until 2026-08-02, verbatim — kept as the
# equivalence ORACLE for the shared linear scanner it now delegates to. The
# pattern itself is quadratic on class-character walls (CodeQL alert #5;
# measured 3.7 s on `"a." * 8000`), which is why it was retired — so it is
# only ever run on short inputs here.
_ORACLE_RE = re.compile(
    r"(?<![/.\-])"
    r"(?P<path>"
    r"(?:[A-Za-z]:[\\/])?"
    r"(?:/)?"
    r"(?:\.{1,2}[\\/])?"
    r"(?:[A-Za-z0-9_.-]+[\\/])*"
    r"[A-Za-z0-9_.-]+\."
    r"(?:py|md|txt|json|yml|yaml|pdf|jsonl|toml|ini|cfg|log)"
    r")",
    flags=re.IGNORECASE,
)


def _oracle(text: str) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for match in _ORACLE_RE.finditer(text or ""):
        path = match.group("path").rstrip(".,;:!?)\"]}'")
        key = path.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return tuple(out)


def test_paths_mentioned_matches_the_retired_regex_on_recorded_traps() -> None:
    for text in [
        "сколько строк в core/loop.py и tools/base.py",
        "объясни разницу между REST и GraphQL",
        "прочитай data/events.jsonl и config.toml, потом app.ini и run.cfg",
        "лог в daemon.log, а данные в a.json и b.jsonl",
        "x.jsonl",                      # alternation order: "json" wins, "l" stays out
        "a//b.py", "C://a.py", "backslash\\style\\file.md",
        "de.py/x", "-C:/a.py", "aC:/x.py", ".../a.md",
    ]:
        assert paths_mentioned(text) == _oracle(text), repr(text)


def test_paths_mentioned_matches_the_retired_regex_under_fuzz() -> None:
    """Hash-driven corpus, same generator discipline as the loop twin's tests."""
    import hashlib
    import itertools
    import string

    alphabets = [
        string.ascii_letters + string.digits + "./\\-_ .:()'\"!?,;",
        "a./\\-: ", "ab.jsonl/\\:-", "Cc:/\\a.tomlogcfgini ",
    ]
    for alphabet in alphabets:
        for i in range(1000):
            digest = hashlib.sha256(f"obl-{alphabet[:4]}-{i}".encode()).digest()
            stream = itertools.cycle(digest)
            text = "".join(
                alphabet[next(stream) % len(alphabet)]
                for _ in range(digest[0] % 61)
            )
            assert paths_mentioned(text) == _oracle(text), repr(text)


def test_paths_mentioned_stays_cheap_on_a_class_character_wall() -> None:
    """The half the separator-wall guard missed (CodeQL alert #5): starts
    after LETTERS were still open, so `"a." * 8000` cost 3.7 s before this
    delegation and the old pattern fails this bound by 7x.
    """
    for shape in ("a." * 8000, "a" * 16000):
        start = time.perf_counter()
        paths_mentioned(shape)
        assert time.perf_counter() - start < 0.5


def test_paths_mentioned_stays_cheap_on_a_wall_of_separators() -> None:
    """A run of separators must not cost quadratic time to scan.

    `paths_mentioned` reads free text written by the user *and* by the model,
    and nothing upstream caps its length. The pattern was allowed to start a
    match inside a run of `/`, `.` and `-`, so n separators offered n starting
    positions and each one rescanned the rest of the string. Measured on this
    input before the fix: 0.29 s at 4.5 KB, 1.16 s at 9 KB, 4.68 s at 18 KB —
    a clean quadratic curve, and 15.5 s at 38 KB. That is a live hang of the
    whole loop, not a theoretical one.

    The bound below is deliberately loose: after the fix this scan takes about
    a millisecond, so a second still leaves three orders of magnitude of slack
    for a loaded machine.
    """
    text = "/" + "-/-" * 6000

    start = time.perf_counter()
    found = paths_mentioned(text)
    elapsed = time.perf_counter() - start

    # Separators alone are not a path — the answer was, and stays, empty.
    assert found == ()
    assert elapsed < 1.0, f"scanning {len(text)} chars took {elapsed:.2f}s"

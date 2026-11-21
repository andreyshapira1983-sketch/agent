"""Знакомая ошибка приносит с собой то, что помогло в прошлый раз (24.09).

Замер по 446 ходам сервера: 70% провалов — подписи, уже встречавшиеся в других
ходах; «No module named 'core'» — 32 раза за четыре дня. Внутри хода агент
выкручивался, в следующем ходу ошибался заново: память доставалась только в
начале хода по тексту вопроса. Здесь проверено, что урок рождается из пары
«провал → удача», встаёт в причину сбоя (не в улики), судится следующим вызовом
и снимается, если не помогает.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.failure_cards import (
    CardStore,
    experience_notes,
    failed_output_reason,
    learn_after_turn,
    learn_from_events,
    note_for,
    with_past_experience,
    witness_note,
)
from core.observation_round import format_observations
from core.replan import ReplanTrigger

_ERR = "Traceback (most recent call last):\n  File \"<x>\", line 3\nModuleNotFoundError: No module named 'core'"


class _Llm:
    def __init__(self, answer: str) -> None:
        self.answer, self.calls = answer, 0

    def complete(self, **_kw):
        self.calls += 1
        return self.answer


class _Log:
    def __init__(self, path: Path | None = None) -> None:
        self.events: list[tuple[str, dict]] = []
        self.path, self.trace_id = path, "trace_x"

    def log(self, event, payload):
        self.events.append((event, payload))


class _Loop:
    def __init__(self, ws: Path, log: _Log | None = None) -> None:
        self._ws, self.log = ws, log or _Log()

    def _file_read_workspace_root(self):
        return self._ws


def _events(*steps: tuple[str, dict, dict]) -> list[dict]:
    out = []
    for i, (tool, args, result) in enumerate(steps):
        ts = i + 1
        out.append({"event": "tool_call", "ts": f"2026-09-2{ts % 9}T00:00:{ts:02d}",
                    "payload": {"id": f"c{i}", "tool_name": tool, "arguments": args}})
        out.append({"event": "tool_result", "ts": f"2026-09-2{ts % 9}T00:00:{ts:02d}", "trace_id": "t1",
                    "payload": {"tool_call_id": f"c{i}", **result}})
    return out


def _probe_fail() -> dict:
    return {"status": "success", "output": {"exit_code": 1, "stderr": _ERR}}


def _probe_ok() -> dict:
    return {"status": "success", "output": {"exit_code": 0, "stdout": "42"}}


def test_a_failure_then_a_success_leaves_a_lesson_that_returns_with_the_error(tmp_path: Path) -> None:
    lesson = "Когда python_probe даёт «No module named 'core'» — передай файлы через inputs"
    llm = _Llm(lesson)
    learn_from_events(tmp_path, _events(("python_probe", {"code": "import core"}, _probe_fail()),
                                        ("python_probe", {"code": "open('x')"}, _probe_ok())), llm)
    assert llm.calls == 1
    note = note_for(tmp_path, "python_probe", _ERR)
    assert note and lesson in note

    loop = _Loop(tmp_path)
    trig = ReplanTrigger(code="tool_error", step_id="s", tool_name="python_probe",
                         arguments={}, reason="exit_code=1\n" + _ERR, attempt=1)
    shown = with_past_experience(loop, trig)
    assert lesson in shown.reason and shown.reason.startswith(trig.reason)
    assert loop.log.events[0][0] == "failure_card_shown"


def test_no_lesson_without_a_success_or_when_the_model_says_unrelated(tmp_path: Path) -> None:
    llm = _Llm("НЕТ")
    learn_from_events(tmp_path, _events(("python_probe", {}, _probe_fail())), llm)
    assert llm.calls == 0, "no later success — nothing to learn from"
    learn_from_events(tmp_path, _events(("python_probe", {}, _probe_fail()), ("python_probe", {}, _probe_ok())), llm)
    assert llm.calls == 1
    assert note_for(tmp_path, "python_probe", _ERR) is None


def test_a_lesson_that_does_not_help_is_retired(tmp_path: Path) -> None:
    learn_from_events(tmp_path, _events(("python_probe", {}, _probe_fail()), ("python_probe", {}, _probe_ok())),
                      _Llm("Когда X — делай Y"))
    card = next(iter(CardStore(tmp_path).cards.values()))
    later = [{"event": e["event"], "ts": "2026-09-30T00:00:0" + e["ts"][-1], "trace_id": "t2",
              "payload": e["payload"]} for e in _events(("python_probe", {}, _probe_fail()),
                                                        ("python_probe", {}, _probe_fail()),
                                                        ("python_probe", {}, _probe_fail()))]
    learn_from_events(tmp_path, later, None)
    card = CardStore(tmp_path).cards[card.sig]
    assert card.misses >= 2 and card.status == "retired"
    assert note_for(tmp_path, "python_probe", _ERR) is None


def test_a_helpful_lesson_is_counted_as_help(tmp_path: Path) -> None:
    learn_from_events(tmp_path, _events(("python_probe", {}, _probe_fail()), ("python_probe", {}, _probe_ok())),
                      _Llm("Когда X — делай Y"))
    again = [{**e, "ts": "2026-09-30" + e["ts"][10:]} for e in
             _events(("python_probe", {}, _probe_fail()), ("python_probe", {}, _probe_ok()))]
    learn_from_events(tmp_path, again, None)
    card = next(iter(CardStore(tmp_path).cards.values()))
    assert (card.hits, card.misses, card.seen) == (1, 0, 2)
    assert "урок помог 1 из 1" in note_for(tmp_path, "python_probe", _ERR)


def test_the_same_error_ten_times_is_one_card_not_ten(tmp_path: Path) -> None:
    """Слово оператора 24.09: рефлексию выключили, потому что она писала дубль на
    дубль и засоряла память (аудит 03.09: 625 записей за один день). Здесь
    повтор ошибки только прибавляет счётчик, а урок пишется один раз."""
    llm = _Llm("Когда X — делай Y")
    for n in range(10):
        ev = _events(("python_probe", {}, _probe_fail()), ("python_probe", {}, _probe_ok()))
        learn_from_events(tmp_path, [{**e, "ts": f"2026-09-{10 + n}" + e["ts"][10:]} for e in ev], llm,
                          trace_id=f"t{n}")
    cards = CardStore(tmp_path).cards
    assert len(cards) == 1 and llm.calls == 1
    assert next(iter(cards.values())).seen == 10


def test_a_write_that_did_not_compose_is_learned_too(tmp_path: Path) -> None:
    """Живой замер 24.09: «в файл правки пошёл текст без блоков» трижды за ход,
    карточек ноль — событие сборки не было вызовом инструмента."""
    err = "RuntimeError: в файл правки пошёл текст без блоков (FILE: и <<<<<<< SEARCH)"
    ev = [{"event": "write_compose_failed", "ts": "2026-09-24T16:06:20", "trace_id": "t1",
           "payload": {"step": 5, "path": "proposals/selffix/x/edits.txt", "error": err}},
          *_events(("file_write", {"path": "proposals/selffix/x/edits.txt", "content": "FILE: a"},
                    {"status": "success", "output": {"written": True}}))]
    ev[1]["ts"] = ev[2]["ts"] = "2026-09-24T16:07:00"
    learn_from_events(tmp_path, ev, _Llm("Когда file_write не собрал правку без блоков — начни с FILE:"))
    trig = ReplanTrigger(code="tool_error", step_id="s", tool_name="file_write", arguments={},
                         reason=f"текст записи не собрался: {err}", attempt=1)
    assert "начни с FILE:" in with_past_experience(_Loop(tmp_path), trig).reason


def test_a_failed_shell_command_keeps_its_stderr() -> None:
    reason = failed_output_reason({"exit_code": 2, "stderr": "grep: core/x.py: No such file"})
    assert "exit_code=2" in reason and "No such file" in reason
    assert failed_output_reason(None) == "tool execution failed"


def test_a_failing_test_names_what_it_guards(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_w.py").write_text(
        'def test_guard():\n    """MIR-042: a stale lesson must not be shown."""\n', encoding="utf-8")
    note = witness_note(tmp_path, "FAILED tests/test_w.py::test_guard - AssertionError")
    assert note and "MIR-042" in note


def test_the_note_reaches_the_planner_but_not_the_evidence(tmp_path: Path) -> None:
    learn_from_events(tmp_path, _events(("python_probe", {}, _probe_fail()), ("python_probe", {}, _probe_ok())),
                      _Llm("Когда X — делай Y"))
    artifacts = {"step_1": {"tool": "python_probe", "output": _probe_fail()["output"]}}
    notes = experience_notes(_Loop(tmp_path), artifacts)
    block = format_observations(None, artifacts, notes=notes)
    assert "Когда X — делай Y" in block
    assert "Когда X" not in json.dumps(artifacts, ensure_ascii=False), "the tool output stays untouched"


def test_the_operator_brake_on_auto_memory_stops_learning(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    trace.write_text("\n".join(json.dumps(e) for e in _events(("python_probe", {}, _probe_fail()))),
                     encoding="utf-8")

    class _Frozen:
        frozen_sources = frozenset({"agent-auto"})

    loop = _Loop(tmp_path, _Log(trace))
    loop.write_policy = _Frozen()
    learn_after_turn(loop)
    assert not (tmp_path / "data" / "failure_cards.jsonl").exists()
    loop.write_policy = None
    learn_after_turn(loop)
    assert (tmp_path / "data" / "failure_cards.jsonl").exists()


def test_a_loop_without_a_workspace_writes_no_cards_where_it_runs(tmp_path: Path, monkeypatch) -> None:
    """Запасной `Path.cwd()` писал data/failure_cards.jsonl в папку процесса:
    тест без file_read оставил его в копии /root/verify, и соседний замер живого
    состояния упал (2026-09-24). Нет рабочей папки — нет и карточек."""
    trace = tmp_path / "trace.jsonl"
    events = _events(("python_probe", {}, _probe_fail()), ("python_probe", {}, _probe_ok()))
    trace.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    run_dir = tmp_path / "cwd"
    run_dir.mkdir()
    monkeypatch.chdir(run_dir)

    class _Bare:
        log = _Log(trace)

    learn_after_turn(_Bare())
    trig = ReplanTrigger(code="tool_error", step_id="s", tool_name="python_probe", arguments={},
                         reason=_ERR, attempt=0)
    assert with_past_experience(_Bare(), trig) is trig
    assert experience_notes(_Bare(), {"x": {"tool": "python_probe", "output": _probe_fail()["output"]}}) == {}
    assert not (run_dir / "data").exists()

def test_the_loop_uses_it_where_failures_are_read() -> None:
    import inspect

    from core import loop_attempt, loop_memory_write, loop_step_execution, observation_round

    assert "attempt_failures.append(with_past_experience(self, trigger))" in inspect.getsource(loop_attempt)
    assert "notes=experience_notes(loop, attempt_artifacts)" in inspect.getsource(observation_round)
    assert "failed_output_reason(result.output)" in inspect.getsource(loop_step_execution)
    assert "learn_after_turn(self)" in inspect.getsource(loop_memory_write)

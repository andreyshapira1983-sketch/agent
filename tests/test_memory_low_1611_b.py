"""Мелкие дефекты памяти уроков, самосборки, шаблонов и бюджета (проверка 16.11, мелкая)."""
from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from core import failure_cards as fc
from core import self_build_memory as sbm
from core.evidence_budget import apply_total_budget
from core.failure_cards import CardStore, learn_after_turn, learn_from_events, witness_note
from core.self_build_memory import (
    build_self_build_episode,
    recent_self_build_lessons,
    record_self_build_episode,
)
from core.smart_memory import EpisodicMemoryStore, ProcedureRecord
from core.workflow_memory import experience_lines


def _events(*results):
    out = []
    for i, res in enumerate(results, 1):
        ts = f"2026-10-01T00:00:{i:02d}"
        out += [{"event": "tool_call", "ts": ts, "payload": {"id": f"c{i}", "tool_name": "web_fetch",
                                                            "arguments": {"url": str(i)}}},
                {"event": "tool_result", "ts": ts, "payload": {"tool_call_id": f"c{i}", **res}}]
    return out


_FAIL = {"status": "error", "error": "HTTPError: 404 Not Found"}
_OK = {"status": "success", "output": {"text": "ok"}}


# ── 1. свидетель не читает файл вне tests/ ─────────────────────────────────

def test_witness_note_refuses_paths_outside_workspace_tests(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / "tests").mkdir(parents=True)
    (tmp_path / "outside.py").write_text('"""SECRET outside"""\n', encoding="utf-8")
    (ws / "core.py").write_text('"""inside but not a test"""\n', encoding="utf-8")
    assert witness_note(ws, "FAILED tests/../../outside.py::x - AssertionError") is None
    assert witness_note(ws, "FAILED tests/../core.py::x - AssertionError") is None
    (ws / "tests" / "test_a.py").write_text('def test_x():\n    """guards H-1"""\n', encoding="utf-8")
    note = witness_note(ws, "FAILED tests/test_a.py::test_x - AssertionError")
    assert note is not None and "H-1" in note


# ── 2. сбой урока не глотается молча ───────────────────────────────────────

class _BoomLlm:
    def complete(self, **kw):
        raise RuntimeError("model down")


def test_lesson_failure_is_recorded_and_logged(tmp_path: Path) -> None:
    store = learn_from_events(tmp_path, _events(_FAIL, _OK), _BoomLlm(), trace_id="t")
    assert store.lesson_errors and "model down" in store.lesson_errors[0]

    logged: list[tuple[str, dict]] = []
    log_path = tmp_path / "log.jsonl"
    import json
    log_path.write_text("\n".join(json.dumps(e) for e in _events(_FAIL, _OK)) + "\n", encoding="utf-8")
    ws2 = tmp_path / "ws2"
    ws2.mkdir()
    loop = NS(write_policy=None,
              log=NS(path=log_path, trace_id="t2", log=lambda ev, p: logged.append((ev, p))),
              _file_read_workspace_root=lambda: ws2,
              model_router=NS(for_role=lambda role: _BoomLlm()))
    learn_after_turn(loop)
    assert any(ev == "failure_card_lesson_error" and "model down" in p.get("error", "")
               for ev, p in logged)


# ── 3. замок не держится во время вопроса модели ───────────────────────────

def test_llm_is_asked_without_the_cards_lock_and_no_update_is_lost(tmp_path: Path) -> None:
    path = tmp_path / fc.CARDS_RELPATH
    held: list[bool] = []

    class Llm:
        def complete(self, **kw):
            held.append(fc._lock_held(path))
            # Другой писатель добавляет карточку, пока модель думает.
            other = CardStore(tmp_path)
            other.cards["y|B"] = fc.Card(sig="y|B", tool="y", error="B")
            other.save()
            return "Когда web_fetch даёт 404 — проверь адрес"

    store = learn_from_events(tmp_path, _events(_FAIL, _OK), Llm(), trace_id="t")
    assert held == [False]
    on_disk = CardStore(tmp_path)
    assert "y|B" in on_disk.cards
    active = [c for c in on_disk.cards.values() if c.status == "active"]
    assert len(active) == 1 and active[0].asked == 1 and active[0].seen == 1
    assert on_disk.digested.get("t") == 2
    assert store.lesson_errors == []
    # Тот же ход второй раз ничего не считает заново.
    learn_from_events(tmp_path, _events(_FAIL, _OK), Llm(), trace_id="t")
    assert [c.seen for c in CardStore(tmp_path).cards.values() if c.status == "active"] == [1]


# ── 4. урок файла ищется по целому пути ────────────────────────────────────

def test_lesson_lookup_matches_whole_path_tokens(tmp_path: Path) -> None:
    store = EpisodicMemoryStore(tmp_path / "e.jsonl")
    store.save(build_self_build_episode("self-apply-run", {
        "status": "rolled_back", "reason": "targeted tests failed in tests/core/a.py.bak",
        "files_changed": ["tests/x.py"]}))
    agent = NS(episodic_store=store)
    assert recent_self_build_lessons(agent, "core/a.py") == []
    assert recent_self_build_lessons(agent, "a.py") == []
    store.save(build_self_build_episode("self-apply-run", {
        "status": "rolled_back", "reason": "targeted tests failed in core/a.py.",
        "files_changed": ["tests/x.py"]}))
    found = recent_self_build_lessons(agent, "core/a.py")
    assert len(found) == 1 and "core/a.py." in found[0]


# ── 5. сбой хранилища виден ────────────────────────────────────────────────

def test_storage_failure_is_logged_not_silent(caplog: pytest.LogCaptureFixture) -> None:
    class Boom:
        def load(self):
            raise OSError("disk")

        def save(self, e):
            raise OSError("disk")

    before = sbm.STORE_FAILURES["count"]
    with caplog.at_level(logging.WARNING, logger="core.self_build_memory"):
        ok = record_self_build_episode(NS(episodic_store=Boom()), kind="self-apply-run",
                                       result={"status": "rolled_back"})
    assert ok is False
    assert sbm.STORE_FAILURES["count"] == before + 1
    assert "OSError" in sbm.STORE_FAILURES["last"]
    assert any("disk" in r.getMessage() or r.exc_info for r in caplog.records)


# ── 6. в выведение шаблонов идут только действующие процедуры ──────────────

@pytest.mark.parametrize("status", ["needs_review", "candidate", "obsolete"])
def test_only_active_procedures_feed_induction(status: str) -> None:
    q = "исправь ошибку в тесте"
    proc = ProcedureRecord(name="n", workflow_key="k", trigger_tags=(), steps=("s",),
                           lessons=("unverified lesson L",), source_questions=(q,), status=status)
    assert experience_lines([], [proc]) == {}
    good = ProcedureRecord(name="n", workflow_key="k", trigger_tags=(), steps=("s",),
                           lessons=("verified lesson",), source_questions=(q,), status="active")
    got = experience_lines([], [good])
    assert any("verified lesson" in lines for lines in got.values())


# ── 7. бюджет не выполнен — флаг это говорит ───────────────────────────────

def test_over_budget_at_floor_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_EVIDENCE_TOTAL_CHARS", "1000")
    blocks = [(f"file{i}", "x" * 120) for i in range(30)]
    out, flag = apply_total_budget(blocks)
    assert sum(len(c) for _, c in out) > 1000
    assert flag is True

"""Выдержка из журнала обязана нести содержимое события, а не оглавление.

Background: docs/CODE_NOTES.md, "The excerpt that was a table of contents".
"""
from __future__ import annotations

from core.evidence import MAX_EXCERPT_CHARS, evidence_from_tool_result

_EVENT = {
    "event": "error",
    "payload": {
        "message": "FileNotFoundError: File not found: core/diagnostics.py",
        "code": "file_not_found",
        "recoverable": False,
    },
}


def _evidence(events: list, **extra):
    return evidence_from_tool_result(
        tool_name="read_logs",
        output={"trace_id": "trace_x", "events": events, **extra},
        arguments={},
    )


def test_the_measured_blindness_is_gone():
    """Живой замер 2026-08-15: выдержка «error: <ts>» выбрасывала payload, и
    каждое верное утверждение о содержании записи опровергалось гейтом (c) —
    его литералов не было в улике ПО ПОСТРОЕНИЮ.
    """
    excerpt = _evidence([_EVENT]).excerpt

    assert "File not found: core/diagnostics.py" in excerpt
    assert "file_not_found" in excerpt
    assert "recoverable" in excerpt


def test_the_event_name_is_still_there():
    """Улов не отдан: имя события остаётся сверяемым, как и раньше."""
    assert '"error"' in _evidence([_EVENT]).excerpt


def test_the_excerpt_stays_bounded():
    """Граница объёма живёт в `make_evidence`, и она обязана держать даже
    двадцать раздутых событий.
    """
    fat = {"event": "error", "payload": {"message": "x" * 3000}}

    excerpt = _evidence([fat] * 20).excerpt

    assert len(excerpt) <= MAX_EXCERPT_CHARS + len("...[truncated]")
    assert excerpt.endswith("...[truncated]")


def test_an_unserialisable_event_falls_back_instead_of_breaking():
    """Кривое событие не роняет улику: строка «имя: время» хуже содержимого,
    но лучше отсутствия записи.
    """
    weird = {"event": "error", "ts": "2026-08-15", "payload": {"bad": object()}}

    excerpt = _evidence([weird]).excerpt

    assert excerpt  # улика построена
    assert "error" in excerpt


def test_empty_logs_keep_their_weak_evidence_shape():
    """Пустой журнал — по-прежнему слабая улика с пустой выдержкой, а не
    ошибка: «ничего не случилось в окне X» тоже информация.
    """
    ev = _evidence([])

    assert ev.excerpt == ""
    assert ev.source_id.endswith(":empty")

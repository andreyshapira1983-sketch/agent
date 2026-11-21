"""Выдержка из журнала обязана нести содержимое события, а не оглавление.

Background: docs/CODE_NOTES.md, "The excerpt that was a table of contents".
"""
from __future__ import annotations

from core.evidence import evidence_from_tool_result


def _evidence(events: list, **extra):
    return evidence_from_tool_result(
        tool_name="read_logs",
        output={"trace_id": "trace_x", "events": events, **extra},
        arguments={},
    )


def test_empty_logs_keep_their_weak_evidence_shape():
    """Пустой журнал — по-прежнему слабая улика с пустой выдержкой, а не
    ошибка: «ничего не случилось в окне X» тоже информация.
    """
    ev = _evidence([])

    assert ev.excerpt == ""
    assert ev.source_id.endswith(":empty")

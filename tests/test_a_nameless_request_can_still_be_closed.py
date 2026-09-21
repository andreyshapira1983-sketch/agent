"""Строку без идентификатора в ящике одобрений можно закрыть.

2026-09-21. Агент дописал в `data/approval_inbox.jsonl` строку без `id`
(journal_append, теперь это запрещено). `ApprovalInboxItem.from_dict` давал
такой строке `new_id("ain")` — НОВЫЙ случайный идентификатор при каждом
чтении. `set_status` перечитывает файл перед правкой, получает другой
идентификатор и отвечает «approval not found». Строка висела заявкой, у неё
не было срока, и снять её штатно было нельзя ни человеку, ни коду: засов,
к которому нельзя обратиться дважды. Она держала полосу самоправки закрытой
навсегда — `core/self_apply_lane.py` останавливается при любой висящей
заявке.

Идентификатор такой строки теперь выводится из её содержимого: одна и та же
строка — одно и то же имя при любом чтении.
"""
from __future__ import annotations

from core.approval_inbox import ApprovalInbox
from core.state_integrity import append_state_jsonl_unlocked


def _inbox_with_a_nameless_row(tmp_path):
    path = tmp_path / "approval_inbox.jsonl"
    append_state_jsonl_unlocked(path, [{
        "kind": "code_patch_request", "author": "agent",
        "summary": "заявка без имени", "title": "t",
    }])
    return path


def test_the_nameless_row_has_the_same_name_on_every_read(tmp_path) -> None:
    path = _inbox_with_a_nameless_row(tmp_path)
    first = [i.id for i in ApprovalInbox(path=path).items]
    second = [i.id for i in ApprovalInbox(path=path).items]
    assert first == second


def test_the_nameless_row_can_be_closed(tmp_path) -> None:
    path = _inbox_with_a_nameless_row(tmp_path)
    box = ApprovalInbox(path=path)
    assert len(box.pending()) == 1
    box.set_status(box.pending()[0].id, "aborted", decided_by="test",
                   decision_reason="не заявка")
    assert ApprovalInbox(path=path).pending() == []

"""Доказанный дубль сводится раскольщиком при любом размере файла, без модели.

Эпизод 2026-09-21 13:44: руки взяли core/scheduler.py с диагнозом «дубль», но
файл в 541 строку меньше порога 900, и правку писала модель-Строитель одним
выстрелом. Она выбросила _FALSE_VALUES, _TRUE_VALUES, _VALID_STATUSES, критик
верно отказал — трижды, и правка не получалась никогда.
"""
from __future__ import annotations

from pathlib import Path

from core.self_build_producer import produce_self_apply_proposal

_TWIN = '''
def _parse_iso(value):
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        from datetime import datetime
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        return None
    return stamp
'''


class _Inbox:
    def __init__(self):
        self.added: list[dict] = []

    def list(self, **_k):
        return []

    def pending(self):
        return []

    def add(self, **kwargs):
        self.added.append(kwargs)
        return type("Item", (), {"id": "ain_test", "status": "pending"})()


class _NoModel:
    def complete(self, *_a, **_k):
        raise AssertionError("доказанный дубль не должен звать модель")


def test_a_small_proven_duplicate_goes_to_the_splitter(tmp_path: Path) -> None:
    core = tmp_path / "core"
    core.mkdir()
    (core / "split_proof.py").write_text("", encoding="utf-8")
    (core / "queue_store.py").write_text(_TWIN, encoding="utf-8")
    (core / "sched.py").write_text(
        "from core.queue_store import _parse_iso as _unused\n"
        "_KEEP_ME = frozenset({'on'})\n" + _TWIN, encoding="utf-8")
    inbox = _Inbox()
    report = produce_self_apply_proposal(
        workspace=tmp_path, inbox=inbox, llm=_NoModel(), candidate_targets=["core/sched.py"])
    assert report.status == "proposed", report.reason
    assert "dedup" in report.reason
    assert len(inbox.added) == 1

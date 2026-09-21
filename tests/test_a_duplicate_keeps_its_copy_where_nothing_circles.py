"""Дом копии при сведении дубля не зависит от модуля, из которого копию убирают.

Эпизод 2026-09-21 08:41: цель велела оставить `_bool`/`_parse_iso` в
core/scheduler.py и взять их импортом в core/task_queue.py. Но scheduler.py
сам импортирует task_queue — правка замкнула бы импорт в круг, и task_queue
падал бы при загрузке. Правка идёт с той стороны, которая уже зависит.
"""
from __future__ import annotations

from core.split_proof import index_workspace, proof_for

_BODY = '''
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


def _workspace(tmp_path, files: dict[str, str]):
    (tmp_path / "core").mkdir()
    for rel, text in files.items():
        (tmp_path / rel).write_text(text, encoding="utf-8")
    return index_workspace(tmp_path)


def test_the_importer_gives_up_its_copy_not_the_imported(tmp_path) -> None:
    index = _workspace(tmp_path, {
        "core/queue.py": _BODY,
        "core/sched.py": "from core.queue import _parse_iso as _unused\n" + _BODY,
    })
    kept = proof_for("core/queue.py", index)
    assert kept is None, "дом копии не может брать её у того, кто импортирует его самого"
    moved = proof_for("core/sched.py", index)
    assert moved is not None and moved.kind == "dup"
    assert moved.other == "core/queue.py"
    assert moved.names == ("_parse_iso",)


def test_a_chain_of_imports_is_a_circle_too(tmp_path) -> None:
    index = _workspace(tmp_path, {
        "core/queue.py": _BODY,
        "core/middle.py": "import core.queue\n",
        "core/sched.py": "from core.middle import something\n" + _BODY,
    })
    assert proof_for("core/queue.py", index) is None
    moved = proof_for("core/sched.py", index)
    assert moved is not None and moved.other == "core/queue.py"


def test_unrelated_twins_may_go_either_way(tmp_path) -> None:
    index = _workspace(tmp_path, {"core/left.py": _BODY, "core/right.py": _BODY})
    assert proof_for("core/left.py", index).other == "core/right.py"
    assert proof_for("core/right.py", index).other == "core/left.py"

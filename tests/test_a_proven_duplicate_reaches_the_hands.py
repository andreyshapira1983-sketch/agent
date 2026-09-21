"""Доказательство переделки доходит от цели до рук, а не только до головы.

Эпизод 2026-09-21, циклы 7 и 17: драйв нашёл настоящий дубль и поставил цель
«свести повтор в core/task_queue.py», а руки ответили
`campaign_engineering_proposed status=no_grounded_target`: они берут работу
только из бэклога, а в бэклоге было семь записей «файл большой» и ни одной
доказанной. Доказанная переделка обязана быть в бэклоге — и выше голого размера.
"""
from __future__ import annotations

from core.backlog_selector import load_backlog
from core.self_build_producer import _default_grounded_selector, _manager_from_grounded

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


def _workspace(tmp_path):
    core = tmp_path / "core"
    core.mkdir()
    (core / "split_proof.py").write_text("", encoding="utf-8")  # признак настоящей копии
    (core / "queue_store.py").write_text(_TWIN, encoding="utf-8")
    (core / "sched.py").write_text(
        "from core.queue_store import _parse_iso as _unused\n" + _TWIN, encoding="utf-8")
    return tmp_path


def test_the_backlog_carries_the_proof(tmp_path) -> None:
    ws = _workspace(tmp_path)
    proven = [c for c in load_backlog(ws) if c.signal_source == "split_proof"]
    assert [c.target_path for c in proven] == ["split:core/sched.py"]
    assert "дословно повторяют core/queue_store.py" in proven[0].problem_quote


def test_a_proof_outranks_a_bare_size(tmp_path) -> None:
    ws = _workspace(tmp_path)
    (ws / "core" / "huge.py").write_text(
        "\n".join(f"def f{i}():\n    return {i}\n" for i in range(700)), encoding="utf-8")
    backlog = load_backlog(ws)
    sources = [c.signal_source for c in backlog]
    assert "oversized_module" in sources, "размер остаётся поводом посмотреть"
    assert sources.index("split_proof") < sources.index("oversized_module")


def test_the_hands_take_the_named_proven_module(tmp_path) -> None:
    ws = _workspace(tmp_path)
    named = frozenset({"core/sched.py"})
    chosen = _manager_from_grounded(
        _default_grounded_selector(ws, only_targets=named), (),
        workspace=ws, named_targets=named)
    assert chosen.decision == "selected", chosen.detail
    assert chosen.data["target"] == "core/sched.py"
    assert chosen.data["diagnosis"].startswith("дубль:")

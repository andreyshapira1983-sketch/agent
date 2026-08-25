"""Слово оператора «эту цель не бери» связывает выбор так же, как своя история.

Замер, отвергнутые варианты и границы: MIR-154 в docs/audit/MASTER_ISSUE_REGISTRY.md.
"""
from __future__ import annotations

import json

from core.charter_goal import CHARTER_RELPATH, VETO_RELPATH, propose_charter_goal

_CHARTER = """# Corporate Model — FUTURE / TARGET
The organisation exists only when roles, authority, budgets, memory boundaries,
verification duties, escalation paths, and accountability are explicit and
enforced.
Human-reserved authority remains in place: merge, budget kill-switch,
governance changes, and approval of escalated actions stay with a human.
"""

_GOAL = (
    "Сделать явными границы памяти: описать, какие записи каких хранилищ "
    "кто производит, в docs/MEMORY_SYSTEM_AUDIT.md"
)


class _LLM:
    def __init__(self, reply: dict) -> None:
        self.reply = reply

    def complete(self, *, system: str, user: str, **_kw) -> str:
        return json.dumps(self.reply)


def _workspace(tmp_path, *, veto: str | None = None):
    path = tmp_path / CHARTER_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_CHARTER, encoding="utf-8")
    ledger = tmp_path / "data" / "campaign_ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("", encoding="utf-8")
    if veto is not None:
        vetoes = tmp_path / VETO_RELPATH
        vetoes.parent.mkdir(parents=True, exist_ok=True)
        vetoes.write_text(veto, encoding="utf-8")
    return tmp_path


def _reply(**overrides) -> dict:
    base = {
        "goal": _GOAL,
        "anchor_id": 0,
        "why_now": "the charter demands explicit boundaries; today they are implicit",
        "success_check": "the document names every store with its producers",
    }
    base.update(overrides)
    return base


def test_a_vetoed_goal_is_declined(tmp_path) -> None:
    """Красный свидетель: своя история агента связывала, слово человека — нет."""
    ws = _workspace(tmp_path, veto=_GOAL + "\n")

    report = propose_charter_goal(_LLM(_reply()), ws)

    assert report.status == "declined", (
        "цель, отозванная оператором, всё равно предложена — история агента "
        "связывает его сильнее, чем слово человека"
    )
    assert "veto" in report.reason.lower() or "оператор" in report.reason.lower()


def test_the_veto_matches_by_the_artifact_the_goal_works_on(tmp_path) -> None:
    """Вето судит предмет работы — тем же правилом, что и повтор своей цели.

    Иначе отозвать цель можно было бы только дословно, а модель формулирует
    её каждый раз заново.
    """
    ws = _workspace(tmp_path, veto="docs/MEMORY_SYSTEM_AUDIT.md\n")

    report = propose_charter_goal(_LLM(_reply()), ws)

    assert report.status == "declined"


def test_an_unrelated_goal_still_passes(tmp_path) -> None:
    """Контроль: без него вето удовлетворялось бы отказом во всём."""
    ws = _workspace(tmp_path, veto="docs/SOMETHING_ELSE.md\n")

    report = propose_charter_goal(_LLM(_reply()), ws)

    assert report.status == "proposed", report.reason


def test_no_veto_file_blocks_nothing(tmp_path) -> None:
    """Отсутствие списка — это отсутствие запретов, а не запрет всего."""
    ws = _workspace(tmp_path, veto=None)

    assert propose_charter_goal(_LLM(_reply()), ws).status == "proposed"


def test_comments_and_blank_lines_are_not_vetoes(tmp_path) -> None:
    """Пустая строка не должна отзывать всё подряд."""
    ws = _workspace(tmp_path, veto="# заметка оператора\n\n   \n")

    assert propose_charter_goal(_LLM(_reply()), ws).status == "proposed"


def test_an_unreadable_veto_list_stops_the_goal(tmp_path) -> None:
    """Нечитаемый список отказывает НАЗВАННО, а не пропускает молча.

    Отзыв, потерянный из-за сбоя чтения, — это возврат отозванной работы без
    ведома человека. Отказ с названной причиной он увидит и починит.
    """
    ws = _workspace(tmp_path, veto=None)
    (ws / VETO_RELPATH).mkdir(parents=True)  # каталог вместо файла: чтение упадёт

    report = propose_charter_goal(_LLM(_reply()), ws)

    assert report.status == "declined"
    assert "veto" in report.reason.lower()

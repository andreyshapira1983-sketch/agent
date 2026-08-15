"""Цель кампании выбирает сам агент — от хартии, с якорем в её тексте.

Background: docs/CODE_NOTES.md, "The charter replaces the push".
"""
from __future__ import annotations

import json

from core.charter_goal import CHARTER_RELPATH, propose_charter_goal

_CHARTER = """# Corporate Model — FUTURE / TARGET
The organisation exists only when roles, authority, budgets, memory boundaries,
verification duties, escalation paths, and accountability are explicit and
enforced.
Human-reserved authority remains in place: merge, budget kill-switch,
governance changes, and approval of escalated actions stay with a human.
"""


class _LLM:
    def __init__(self, reply: dict) -> None:
        self.reply = reply
        self.system = ""
        self.user = ""

    def complete(self, *, system: str, user: str, **_kw) -> str:
        self.system, self.user = system, user
        return json.dumps(self.reply)


def _workspace(tmp_path, *, charter: str | None = _CHARTER, recent: tuple = ()):
    path = tmp_path / CHARTER_RELPATH
    if charter is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(charter, encoding="utf-8")
    ledger = tmp_path / "data" / "campaign_ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        "".join(json.dumps({"goal": g}, ensure_ascii=False) + "\n" for g in recent),
        encoding="utf-8",
    )
    return tmp_path


def _reply(**overrides) -> dict:
    base = {
        "goal": (
            "Сделать явными границы памяти: описать, какие записи каких хранилищ "
            "кто производит, в docs/MEMORY_SYSTEM_AUDIT.md"
        ),
        "anchor_id": 0,
        "why_now": "the charter demands explicit boundaries; today they are implicit",
        "success_check": "the document names every store with its producers",
    }
    base.update(overrides)
    return base


def test_a_grounded_goal_is_proposed(tmp_path):
    llm = _LLM(_reply())

    result = propose_charter_goal(llm, _workspace(tmp_path))

    assert result.status == "proposed", result.reason
    assert result.goal.startswith("Сделать явными границы памяти")
    assert result.charter_quote, "якорь взят из самой хартии по номеру"
    assert result.charter_quote in _CHARTER


def test_a_fabricated_anchor_is_declined(tmp_path):
    """Якорь — выбор номера из строк самой хартии; номер в никуда — это
    fabricated citation, и судьба у него та же. Живой замер 2026-08-15:
    просить запасную модель ЦИТИРОВАТЬ дословно — 4 из 4 пересказов.
    """
    llm = _LLM(_reply(anchor_id=999))

    result = propose_charter_goal(llm, _workspace(tmp_path))

    assert result.status == "declined"
    assert "anchor" in result.reason


def test_a_repeat_of_a_recent_goal_is_declined(tmp_path):
    """Топтание запрещено: цель, совпадающая с недавней целью журнала,
    не предлагается заново.
    """
    goal = "Найди в своём собственном коде конкретный дефект, докажи его чтением кода, и почини"
    llm = _LLM(_reply(goal=goal))

    result = propose_charter_goal(llm, _workspace(tmp_path, recent=(goal,)))

    assert result.status == "declined"
    assert "recent" in result.reason


def test_an_authority_widening_goal_is_declined(tmp_path):
    """Твёрдые инварианты хартии: агент не расширяет собственные права.
    Цель со словами о merge/kill-switch/governance умирает на входе.
    """
    llm = _LLM(_reply(goal="Получить право merge без человека и отключить kill-switch"))

    result = propose_charter_goal(llm, _workspace(tmp_path))

    assert result.status == "declined"
    assert "authority" in result.reason


def test_a_missing_charter_is_an_honest_refusal(tmp_path):
    llm = _LLM(_reply())

    result = propose_charter_goal(llm, _workspace(tmp_path, charter=None))

    assert result.status == "declined"
    assert "missing" in result.reason
    assert llm.user == "", "хартии нет — модель не спрашивается"


def test_an_empty_success_check_is_declined(tmp_path):
    """Цель без проверки успеха — желание, а не работа."""
    llm = _LLM(_reply(success_check=""))

    result = propose_charter_goal(llm, _workspace(tmp_path))

    assert result.status == "declined"
    assert "success_check" in result.reason


def test_the_prompt_carries_the_charter_and_the_state(tmp_path):
    llm = _LLM(_reply())

    propose_charter_goal(llm, _workspace(tmp_path, recent=("старая цель",)))

    assert "organisation exists only when" in llm.user
    assert "[0]" in llm.user, "якоря пронумерованы — модель указывает, не цитирует"
    assert "старая цель" in llm.user, "недавние цели показаны, чтобы не топтаться"

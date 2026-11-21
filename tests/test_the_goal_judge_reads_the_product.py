"""Судья цели читает продукт: каждое «да» — с цитатой, найденной в файле.

Сверка 2026-09-25: «verified» ставилось за то, что названный файл появился; из
873 целей с «результатом» 168 признаны только так. Agent-as-a-Judge
(arXiv 2410.10934) и TICK (arXiv 2410.03608): судья сам открывает артефакт и
проверяет требования по одному, вопросами «да/нет».
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from core.campaign_verdict import judge_campaign

_CHECK = "docs/stores.md names every memory store and the module that writes it"
_TEXT = ("# Memory stores\n\n- persistent_memory.jsonl is written by core/loop_memory_write.py\n"
         "- episodic_memory.jsonl is written by core/smart_memory.py\n")


class _Judge:
    def __init__(self, rows) -> None:
        self.rows, self.calls = rows, 0

    def complete(self, *, system: str, user: str, **_kw) -> str:
        self.calls += 1
        assert "FILE CONTENT" in user and "persistent_memory.jsonl" in user, "the judge must see the file itself"
        return self.rows if isinstance(self.rows, str) else json.dumps(self.rows)


def _ws(tmp_path: Path) -> tuple[Path, float]:
    since = time.time() - 5
    (tmp_path / "docs").mkdir(parents=True)
    (tmp_path / "docs" / "stores.md").write_text(_TEXT, encoding="utf-8")
    return tmp_path, since


def _judge(tmp_path: Path, llm) -> dict:
    ws, since = _ws(tmp_path)
    return judge_campaign(goal="document the stores", success_check=_CHECK, workspace=ws, since=since, llm=llm)


def test_a_yes_whose_quote_is_not_in_the_file_is_a_no(tmp_path: Path) -> None:
    llm = _Judge([{"question": "Does it name the procedural store?", "answer": "yes",
                   "quote": "procedural_memory.jsonl is written by core/smart_memory.py"}])
    verdict = _judge(tmp_path, llm)
    assert verdict["verdict"] == "unmet" and "procedural store" in verdict["reason"]


def test_without_a_model_or_with_an_unreadable_reply_the_file_verdict_stands(tmp_path: Path) -> None:
    assert _judge(tmp_path, None)["verdict"] == "verified"
    assert _judge(tmp_path / "b", _Judge("sorry, I cannot"))["verdict"] == "verified"


def test_an_old_file_is_not_sent_to_the_judge(tmp_path: Path) -> None:
    ws, _ = _ws(tmp_path)
    llm = _Judge([])
    verdict = judge_campaign(goal="g", success_check=_CHECK, workspace=ws, since=time.time() + 60, llm=llm)
    assert verdict["verdict"] == "preexisting" and llm.calls == 0


def test_the_judges_own_file_header_is_not_a_quote(tmp_path: Path) -> None:
    """Живая проба 2026-09-25: на «файл создан?» судья процитировал заголовок `=== FILE … ===`."""
    llm = _Judge([{"question": "Is the file there?", "answer": "yes", "quote": "=== FILE docs/stores.md ==="}])
    assert _judge(tmp_path, llm)["verdict"] == "unmet"

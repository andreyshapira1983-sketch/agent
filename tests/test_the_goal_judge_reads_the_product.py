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

from core.campaign_verdict import VERDICTS, judge_campaign

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


def test_a_requirement_backed_by_a_quote_from_the_file_keeps_verified(tmp_path: Path) -> None:
    llm = _Judge([{"question": "Does it name persistent memory and its writer?", "answer": "yes",
                   "quote": "persistent_memory.jsonl is written by core/loop_memory_write.py"}])
    assert _judge(tmp_path, llm)["verdict"] == "verified" and llm.calls == 1


def test_a_yes_whose_quote_is_not_in_the_file_is_a_no(tmp_path: Path) -> None:
    llm = _Judge([{"question": "Does it name the procedural store?", "answer": "yes",
                   "quote": "procedural_memory.jsonl is written by core/smart_memory.py"}])
    verdict = _judge(tmp_path, llm)
    assert verdict["verdict"] == "unmet" and "procedural store" in verdict["reason"]


def test_a_no_is_unmet_and_named(tmp_path: Path) -> None:
    llm = _Judge([{"question": "Does it name the failure cards store?", "answer": "no", "quote": ""},
                  {"question": "Does it name episodic memory?", "answer": "yes",
                   "quote": "episodic_memory.jsonl is written by core/smart_memory.py"}])
    verdict = _judge(tmp_path, llm)
    assert verdict["verdict"] == "unmet" and "failure cards" in verdict["reason"]
    assert "unmet" in VERDICTS


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


def test_a_journal_is_read_from_its_end_where_the_new_row_is(tmp_path: Path) -> None:
    """Живая проба 2026-09-25: новая заявка лежала за первыми 12 000 знаков журнала."""
    since = time.time() - 5
    (tmp_path / "data").mkdir()
    old = "".join(json.dumps({"key": f"old_{i}", "pad": "x" * 200}) + "\n" for i in range(200))
    (tmp_path / "data" / "claims.jsonl").write_text(old + json.dumps({"key": "cclaim_new_one"}) + "\n", encoding="utf-8")

    class _Seer:
        def complete(self, *, system: str, user: str, **_kw) -> str:
            assert "cclaim_new_one" in user, "the newest row must be in what the judge sees"
            return json.dumps([{"question": "Is the new claim there?", "answer": "yes",
                                "quote": '{"key": "cclaim_new_one"}'}])

    verdict = judge_campaign(goal="g", success_check="data/claims.jsonl has the claim cclaim_new_one",
                             workspace=tmp_path, since=since, llm=_Seer())
    assert verdict["verdict"] == "verified"

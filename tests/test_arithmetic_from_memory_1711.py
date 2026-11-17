"""«17 × 23» may come from memory: the checklist judges what the human sees, and the number is computed (17.11)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.bootstrap import build_agent
from core.arithmetic_check import arithmetic_receipt
from core.loop import AgentLoop
from core.request_checklist import Checklist, ChecklistItem, judge_contract_checklist

Q = "Сколько будет 17 × 23? Ответь одним числом."
SECTIONED = "Conclusion: 391\nFacts:\n- 17 × 23 = 17 × 20 + 17 × 3 = 340 + 51 = 391\nSources:\n1. general-knowledge"


# 1 -- the number in the answer is checked by computing the question's expression
@pytest.mark.parametrize("question,answer,ok", [
    (Q, SECTIONED, True),
    (Q, "Conclusion: 392", False),
    ("Сколько будет 7 / 2?", "3,5", True),
    ("What is (12 + 8) * 3?", "The result is 60.", True),
    ("Посчитай 2 ** 10", "1024", True),
    ("Чему равно 2 ^ 10?", "1024", True),
    ("Сколько будет 17 x 23", "391", True),
    ("Какая столица Франции?", "Париж", False),            # nothing to compute
    ("Сколько будет 17?", "17", False),                     # no operator
    ("Посчитай 9 ** 99999999", "0", False),                 # refused, not computed
    ("Сколько будет 1 / 0?", "0", False),
    # hunter, 17.11: each of these used to get a false receipt or crash
    ("Сколько будет 1,5 + 2?", "7", False),                  # decimal comma is refused, not misread
    ("Сколько будет 1,000 + 5?", "5", False),
    ("Реши 2+2 и затем 3*3", "4", False),                    # two expressions
    ("Напиши 3-5 слов", "-2", False),                        # no arithmetic cue
    ("Позвони +972-50-123 или 5-3", "799", False),
    ("Сколько будет 10 - 3?", "Conclusion: 3-10=-7 нет 7", False),   # the number is not the stated one
    ("Сколько будет 17 × 23?", "1,000 or 391", False),
    ("Сколько будет 17 × 23?", "Conclusion: 17 × 23 = 391", True),
    (Q, "Conclusion:\n391\nFacts:\n- 17 × 23 = 340 + 51 = 391\nSources:\n1. general-knowledge", True),  # live shape
    (Q, "Conclusion:\n392\nFacts:\n- 17 × 23 = 391", False),
    ("Сколько будет " + "1" + "+1" * 5000 + "?", "5001", False),     # too long: refused, no crash
    ("Сколько будет " + "0.1" + "*10" * 2000 + "?", "0", False),
    ("Сколько будет (-1) ** 0.5 + 1?", "1", False),          # complex: refused
])
def test_arithmetic_receipt(question, answer, ok):
    assert arithmetic_receipt(question, answer) is ok


# 2 -- the checklist judge sees the text the human sees, not the internal sections
class _Judge:
    def __init__(self):
        self.seen = ""

    def complete(self, system, user, **kw):
        self.seen = user
        return json.dumps({"answers": [{"n": 1, "answer": "YES", "why": "one number"}]})


def test_checklist_judges_the_human_text(monkeypatch):
    monkeypatch.setenv("AGENT_HUMAN_CHAT", "1")
    judge = _Judge()
    loop = SimpleNamespace(llm=judge, log=SimpleNamespace(log=lambda *a, **k: None))
    contract = SimpleNamespace(checklist=Checklist(items=(ChecklistItem("Is it a single number?", 90),)))
    assert judge_contract_checklist(loop, Q, SECTIONED, contract) is False
    response = judge.seen.split("RESPONSE:\n", 1)[1].split("\n\nQUESTIONS:", 1)[0]
    assert response.strip() == "391"


# 3 -- banking: a computed match is a measured verification, so the answer may be replayed
def test_banked_arithmetic_answer_is_replayable(tmp_path):
    agent = build_agent(tmp_path, with_memory=True, approval_provider=None)
    agent._record_experience_memory(
        goal_description="g", question=Q, answer=SECTIONED, tools_used=[], source_labels=["general-knowledge"],
        verified_chunks=0, unverified_chunks=0, replan_exhausted=False, declared_completion="achieved")
    ep = agent.episodic_store.load()[-1]
    assert "computed:arithmetic" in ep.source_labels and ep.verified_chunks == 1
    assert AgentLoop._fast_path_allows_replay(ep, 1.0) is True


def test_a_replay_record_gets_no_receipt(tmp_path):
    """The fast path banks its copy with one unverified chunk: a copy is not re-verified (MIR-041)."""
    agent = build_agent(tmp_path, with_memory=True, approval_provider=None)
    agent._record_experience_memory(
        goal_description="g", question=Q, answer=SECTIONED, tools_used=[], source_labels=["memory:ep-1"],
        verified_chunks=0, unverified_chunks=1, replan_exhausted=False)
    ep = agent.episodic_store.load()[-1]
    assert "computed:arithmetic" not in ep.source_labels and ep.verified_chunks == 0


def test_a_wrong_number_is_not_verified(tmp_path):
    agent = build_agent(tmp_path, with_memory=True, approval_provider=None)
    agent._record_experience_memory(
        goal_description="g", question=Q, answer="Conclusion: 392", tools_used=[], source_labels=[],
        verified_chunks=0, unverified_chunks=0, replan_exhausted=False, declared_completion="achieved")
    ep = agent.episodic_store.load()[-1]
    assert ep.verified_chunks == 0 and AgentLoop._fast_path_allows_replay(ep, 1.0) is False

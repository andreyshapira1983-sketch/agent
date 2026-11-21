"""Обращение агента к человеку называет повод и задаёт вопрос.

Ночь 24.09, 00:10–00:12 UTC: пять «Уткнулся: цель … впустую» подряд — ни
повода из списка, ни вопроса, на который можно ответить. Horvitz (CHI 1999):
разговор — чтобы снять ключевую неопределённость; ProAgentBench (arXiv
2602.04482): сначала решить «звать ли», потом «что сказать».
"""
from __future__ import annotations

import pytest

from tools.journal_append import VOICE_PATH, JournalAppendTool

NIGHT = ("Уткнулся: цель «Объяснить наблюдение о себе: детекторы self_contradiction» "
         "впустую 3 раза подряд")


def test_a_complaint_without_reason_or_question_is_refused(tmp_path) -> None:
    tool = JournalAppendTool(workspace_root=tmp_path)
    with pytest.raises(ValueError, match="names its reason"):
        tool.run(path=VOICE_PATH, record={"author": "agent", "text": NIGHT})
    with pytest.raises(ValueError, match="question"):
        tool.run(path=VOICE_PATH, record={"author": "agent", "reason": "stuck", "text": NIGHT})

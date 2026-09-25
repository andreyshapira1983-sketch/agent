"""Один случай — одно обращение к человеку (план субботы, пункт ж; 24.09).

24.09 все пять суточных обращений ушли за две минуты (00:10–00:12), и все —
одна жалоба: «Уткнулся: цель … self_contradiction (sii_5dab4ac83cc87892)…».
Практика оповещений (Alertmanager: отпечаток, repeat_interval): повтор того же
случая внутри окна не отправляется — «одно происшествие, одно сообщение».
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from tools.journal_append import VOICE_PATH, JournalAppendTool

_NOW = dt.datetime.now(dt.timezone.utc).isoformat()
_SAME = [
    ("Уткнулся: цель «Почини свой дефект Investigate recurring detector signal: self_contradiction» "
     "(sii_5dab4ac83cc87892) впустую 2 раз подряд; дефектов взято в починку 18, поставлено правок 0."),
    ("Уткнулся: цель «Investigate recurring detector signal: self_contradiction» (sii_5dab4ac83cc87892) "
     "не двигается. Что уже сделано: прочитан код детектора core/answer_contradiction.py."),
    ("Уткнулся — и на этот раз с замером. Цель «Объяснить наблюдение о себе: детекторы self_contradiction, "
     "action_report_mismatch» впустую 3 раза подряд, «Почини дефект self_contradiction» — 2 раза; "
     "дефектов взято в починку 18, поставлено правок 0."),
]


#: С 25.09 зов обязан назвать повод и спросить (test_a_call_to_the_human_names_its_reason_and_asks.py);
#: здесь проверяется другое — повтор того же случая, — поэтому повод и вопрос одинаковы у всех.
_ASK = " Что пробовать дальше?"


def _say(tool: JournalAppendTool, text: str, *, reason: str = "stuck", ts: str = _NOW) -> None:
    tool.run(path=VOICE_PATH, record={"author": "agent", "reason": reason,
                                      "text": text if "?" in text else text + _ASK, "ts": ts})


def test_the_same_issue_is_said_once(tmp_path: Path) -> None:
    tool = JournalAppendTool(workspace_root=tmp_path)
    _say(tool, _SAME[0])
    for repeat in _SAME[1:]:
        with pytest.raises(PermissionError) as excinfo:
            _say(tool, repeat)
        assert "already said" in str(excinfo.value)


def test_a_different_issue_still_gets_through(tmp_path: Path) -> None:
    tool = JournalAppendTool(workspace_root=tmp_path)
    _say(tool, _SAME[0])
    _say(tool, "Нужен ключ к сайту заказчика: без него не могу проверить выгрузку, пробовал web_fetch — 403. "
               "Дашь ключ?", reason="wall")


def test_the_same_issue_can_be_said_again_the_next_day(tmp_path: Path) -> None:
    tool = JournalAppendTool(workspace_root=tmp_path)
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=30)).isoformat()
    _say(tool, _SAME[0], ts=old)
    _say(tool, _SAME[1])

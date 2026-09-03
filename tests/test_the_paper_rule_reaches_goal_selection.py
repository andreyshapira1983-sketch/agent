"""Правило одной бумаги доходит до выбора цели.

Замер 2026-09-02/03, из-за которого правило появилось: вечерний цикл «произвёл»
823-строчный TARGET-документ, который оказался копией уже лежащего в дереве
с одной испорченной строкой (выпало «changed conditions», склеилось
«narrowerscope»). Семь TARGET-контрактов в knowledge/doctrine/future/ ждут
реализации; документ — самое дешёвое действие модели, и когда инженерная
работа упирается в очередь одобрений, бумага становится свободной валютой:
docs+knowledge удвоились за месяц (37 -> 73 файла).

Слово оператора 2026-09-03 («Делай все четыре», пункт 4): новый TARGET-документ
не предлагается, пока существующие не реализованы; реализация куска или сверка
контракта с измеренной реальностью — можно.

Свидетель проверяет ПРОВОДКУ, не поведение модели: правило написано в системном
промпте выбора цели и реально доезжает до модели (протокол нерва — слова,
которых модель не видела, не могут влиять на её выбор).
"""
from __future__ import annotations

import json

from core.charter_goal import CHARTER_RELPATH, propose_charter_goal

_CHARTER = (
    "# Corporate Model — FUTURE / TARGET\n"
    "The organisation exists only when roles, authority and evidence are "
    "explicit, and approval of escalated actions stays with a human.\n"
)


class _LLM:
    def __init__(self):
        self.prompts: list[str] = []

    def complete(self, *, system: str, user: str, **_kw) -> str:
        self.prompts.append(system + "\n" + user)
        return json.dumps({
            "goal": "Trace one measured defect end to end and record it",
            "anchor_id": 0,
            "why_now": "test",
            "success_check": "test",
        })


def test_the_paper_rule_is_in_the_prompt_the_model_sees(tmp_path):
    charter = tmp_path / CHARTER_RELPATH
    charter.parent.mkdir(parents=True, exist_ok=True)
    charter.write_text(_CHARTER, encoding="utf-8")

    llm = _LLM()
    propose_charter_goal(llm, tmp_path)

    assert llm.prompts, "выбор цели обязан дойти до модели"
    prompt = llm.prompts[0]
    assert "ONE PAPER RULE" in prompt
    assert "knowledge/doctrine/future/" in prompt, (
        "правило обязано называть адрес, по которому лежат нереализованные "
        "контракты, — иначе модель не может проверить себя"
    )

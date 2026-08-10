"""Разбор СВОЕГО ТЕКУЩЕГО хода — тоже саморазбор, и улика у него есть.

ЖИВОЙ СЛУЧАЙ 2026-08-10, первое сообщение сессии. Оператор просил разобрать
обработку ИМЕННО ЭТОГО сообщения: маршрутизацию, определение языка, участие
компонентов. Ответ был подавлен гейтом нехватки улик — 2210 знаков вырезано,
`evidence_score=0.0`, вместо разбора заглушка.

Причина в предикате: `is_self_analysis_turn` требовал предыдущего хода, потому
что задумывался про «объясни свой ПРОШЛЫЙ ответ». Но уликой для разбора
текущего прогона служит не диалог, а `trace` — класс, названный в этой же
таксономии и существующий с первого сообщения. Гейт спрашивал историю там, где
улика лежала в журнале.

Расширение узкое: нужен явный указатель на ТЕКУЩЕЕ («этого сообщения», «своё
фактическое поведение», «this message», «your actual trace»). Обращение во
втором лице сохранено. Обычный вопрос саморазбором не становится.
"""
from __future__ import annotations

import pytest

from core.evidence_classes import is_self_analysis_turn

_LIVE_TRANSLIT = (
    "Proanaliziruy eto soobshchenie kak obychnyy polnocennyy zapros. "
    "Kak ty opredelil yazyk i namerenie soobshcheniya. Kakie komponenty tvoey "
    "sistemy uchastvovali v etom opredelenii. Est li v tvoey fakticheskoy "
    "trasse priznaki takoy oshibki. Proanaliziruy svoe fakticheskoe povedenie "
    "pri obrabotke etogo soobshcheniya."
)
_LIVE_RU = (
    "Проанализируй своё фактическое поведение при обработке этого сообщения. "
    "Какие компоненты твоей системы участвовали в этом определении?"
)
_LIVE_EN = (
    "Analyse your actual behaviour while processing this message. "
    "Are there signs of such an error in your actual trace?"
)


@pytest.mark.parametrize(
    "question", [_LIVE_TRANSLIT, _LIVE_RU, _LIVE_EN],
    ids=["translit", "russian", "english"],
)
def test_the_first_message_may_analyse_its_own_run(question: str) -> None:
    """ГЛАВНОЕ: первый ход сессии, разбор собственной обработки."""
    decision = is_self_analysis_turn(question, has_prior_turn=False)
    assert decision.is_self_analysis, (
        f"разбор текущего прогона отвергнут как {decision.reason!r} — "
        "гейт спросил историю там, где улика в журнале"
    )
    assert decision.reason == "current_run_introspection"


def test_the_prior_turn_path_is_untouched() -> None:
    """ПРЕДУСЛОВИЕ: прежняя дорога не сузилась и не подменена."""
    decision = is_self_analysis_turn(
        "Ты не правильно ответила, тебе надо починить свой ответ",
        has_prior_turn=True,
    )
    assert decision.is_self_analysis
    assert decision.reason == "conversational_correction"


@pytest.mark.parametrize("question", [
    "что в файле doc.txt",
    "проанализируй core/loop.py и скажи что он делает",
    "расскажи про этот проект",
    "какая погода в этом городе",
])
def test_an_ordinary_question_is_not_self_analysis(question: str) -> None:
    """Ломка наоборот: расширение не имеет права проглотить обычный вопрос.

    «этот проект», «этом городе» содержат указатель на текущее, но предметом
    разбора не являются — иначе гейт улик отключался бы почти всегда, и
    подавление сменилось бы противоположным дефектом.
    """
    decision = is_self_analysis_turn(question, has_prior_turn=False)
    assert not decision.is_self_analysis, f"проглочен обычный вопрос: {question!r}"


def test_addressing_the_agent_without_a_subject_is_not_enough() -> None:
    """Второе лицо само по себе саморазбором не делает."""
    assert not is_self_analysis_turn(
        "ты можешь прочитать этот файл?", has_prior_turn=False
    ).is_self_analysis


def test_an_empty_question_is_still_refused() -> None:
    assert not is_self_analysis_turn("", has_prior_turn=False).is_self_analysis

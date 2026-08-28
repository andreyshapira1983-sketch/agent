"""На человеческую фразу — человеческий ответ, не отчёт (ступень 1 разговора).

Заказ оператора 2026-08-28: «если на "привет" придёт программа — это не язык;
если каждые 5 минут одно и то же — это не разговор». Судья чистый и судит
ФОРМУ, не содержание; сочинять ответы ему запрещено конструкцией (урок ELIZA).
Литпроход и границы: docs/audit/CONVERSATION_ORGAN_DESIGN.md.

Красные свидетели: до органа «привет» получал полный Output Contract с
секциями и цитатами, а дубль реплики уходил без стеснения (таймерный спам
старого прототипа — тот же класс, Clippy).
"""
from __future__ import annotations

from core.conversation_contract import classify_register, judge_reply

_CONTRACT_DUMP = (
    "Conclusion: Здравствуйте! Я работаю и готов отвечать.\n"
    "Facts:\n- source_registry держит 810 источников [file:data/x.jsonl]\n"
    "- батарея 9148 зелёных [test:full]\n"
    "Unverified: ничего.\n"
    "Sources: file:data/x.jsonl\nConfidence: 0.97\n"
)


def test_a_greeting_is_small_talk_and_a_question_is_not() -> None:
    assert classify_register("привет") == "small_talk"
    assert classify_register("Привет, как дела?") == "small_talk"
    assert classify_register("hello") == "small_talk"
    assert classify_register(
        "привет, разбери журнал вчерашнего тика и скажи, что пошло не так, "
        "начиная с выбора цели"
    ) == "substantive", "длинная деловая фраза с приветствием — дело, не болтовня"
    assert classify_register("почему упал тест?") == "substantive"


def test_a_greeting_gets_the_human_sentence_not_the_report() -> None:
    judgement = judge_reply("привет", _CONTRACT_DUMP)

    assert judgement.verdict == "trim", judgement.reasons
    assert judgement.human_reply == "Здравствуйте! Я работаю и готов отвечать."
    assert "Facts" not in judgement.human_reply


def test_a_substantive_question_keeps_its_full_answer() -> None:
    """Граница: резать доказательный ответ на деловой вопрос нельзя —
    проверяемость дороже краткости."""
    judgement = judge_reply("почему упал тест интеграции?", _CONTRACT_DUMP)

    assert judgement.verdict == "ok"
    assert judgement.human_reply is None


def test_an_unasked_repeat_is_silenced() -> None:
    """Урок таймерного спама: тот же ответ на ДРУГОЙ вопрос — шум."""
    reply = "Готово: батарея зелёная, обе ветки обновлены."
    judgement = judge_reply(
        "что нового?", reply,
        recent_exchanges=(("как прошёл тик?", reply),),
    )

    assert judgement.verdict == "repeat_silence"


def test_a_repeated_question_earns_its_answer_again() -> None:
    """Граница второго экзаменатора (2026-08-28): человек, повторивший
    вопрос, заслуживает свой ответ снова — глушить его было бы ложным
    срабатыванием правила."""
    reply = "Батарея зелёная: 9148 тестов, ruff на 113."
    judgement = judge_reply(
        "сколько тестов прошло?", reply,
        recent_exchanges=(("сколько тестов прошло?", reply),),
    )

    assert judgement.verdict == "ok", judgement.reasons


def test_a_fact_inversion_is_never_silenced_as_a_repeat() -> None:
    """Находка второго экзаменатора (2026-08-28): токенизатор, режущий «не»,
    отдаёт «тест прошёл» и «тест не прошёл» одним множеством — судья заглушил
    бы именно ИЗМЕНЕНИЕ факта. Тот же класс уже чинился в topic_tokens
    (полярные слова — третье исключение из правила длины); орган обязан пить
    из того же словаря, а не заводить второй."""
    judgement = judge_reply(
        "тест упал?", "Тест не прошёл.",
        recent_exchanges=(("тест прошёл?", "Тест прошёл."),),
    )

    assert judgement.verdict != "repeat_silence", (
        "противоположный по смыслу ответ заглушён как повтор")


def test_thanks_with_a_question_is_business_not_small_talk() -> None:
    """Вторая находка: короткое «спасибо, почему упал тест?» не смеет
    считаться болтовнёй — после вычета формул вежливости остаётся дело."""
    assert classify_register("спасибо, почему упал тест?") == "substantive"
    judgement = judge_reply("спасибо, почему упал тест?", _CONTRACT_DUMP)
    assert judgement.verdict == "ok", (
        "доказательный ответ на деловой вопрос урезан из-за формулы вежливости")


def test_a_fresh_reply_is_not_mistaken_for_a_repeat() -> None:
    judgement = judge_reply(
        "как дела?", "Сегодня закрыл MIR-185 и стёр старую модель толчков.",
        recent_exchanges=(
            ("что нового?", "Готово: батарея зелёная, обе ветки обновлены."),
        ),
    )

    assert judgement.verdict in ("ok", "trim")


def test_an_empty_reply_becomes_honest_silence() -> None:
    assert judge_reply("привет", "   ").verdict == "silence"


def test_a_language_mismatch_is_observed_not_invented() -> None:
    """Механического перевода нет — наблюдение идёт в журнал, ответ не
    подменяется (сочинённая фраза была бы ELIZA)."""
    judgement = judge_reply(
        "почему упал тест?", "The integration test failed because of a timeout."
    )

    assert judgement.verdict == "ok"
    assert judgement.observations, "несовпадение языка обязано быть замечено"
    assert judgement.human_reply is None


def test_the_wire_trims_a_greeting_and_journals_the_verdict(monkeypatch) -> None:
    """Проводка: судья стоит между переводчиком и печатью, вердикт — в журнал."""
    from cli import repl

    class _Log:
        def __init__(self) -> None:
            self.events: list[tuple[str, dict]] = []

        def log(self, event, payload) -> None:
            self.events.append((event, payload))

    class _Agent:
        log = None

    agent = _Agent()
    agent.log = _Log()
    monkeypatch.setattr(repl, "_RECENT_REPLIES", [])

    printed = repl._judged_human_reply(agent, "привет", _CONTRACT_DUMP)

    assert printed == "Здравствуйте! Я работаю и готов отвечать."
    verdicts = [p["verdict"] for e, p in agent.log.events
                if e == "conversation_contract"]
    assert verdicts == ["trim"]

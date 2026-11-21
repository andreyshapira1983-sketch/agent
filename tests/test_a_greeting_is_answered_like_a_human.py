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

from core.conversation_contract import judge_reply

_CONTRACT_DUMP = (
    "Conclusion: Здравствуйте! Я работаю и готов отвечать.\n"
    "Facts:\n- source_registry держит 810 источников [file:data/x.jsonl]\n"
    "- батарея 9148 зелёных [test:full]\n"
    "Unverified: ничего.\n"
    "Sources: file:data/x.jsonl\nConfidence: 0.97\n"
)


def test_a_greeting_gets_the_human_sentence_not_the_report() -> None:
    judgement = judge_reply("привет", _CONTRACT_DUMP)

    assert judgement.verdict == "trim", judgement.reasons
    assert judgement.human_reply == "Здравствуйте! Я работаю и готов отвечать."
    assert "Facts" not in judgement.human_reply


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

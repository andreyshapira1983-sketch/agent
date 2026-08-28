"""Правила человеческого разговора: судья исходящей реплики (ступень 1).

Заказ оператора 2026-08-28: «не хватает второй половины человеческого
разговора — если на "привет" приходит программа, это не язык». Судья чистый
(ни LLM, ни сети): он смотрит на пару «что спросил человек → что собрался
ответить агент» и выносит механический вердикт. Полная история и три ступени
плана — docs/CODE_NOTES.md («The other half of the conversation»).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Регистр «малый разговор»: приветствия, благодарности, прощания, «как дела»
# по-русски и по-английски. Слово целиком (\b), чтобы «приветствие судьи» не
# считалось за «привет»; потолок длины — короткая фраза, не письмо.
_SMALL_TALK_RE = re.compile(
    r"\b(привет|здравствуй(?:те)?|добрый\s+(?:день|вечер)|доброе\s+утро|"
    r"как\s+(?:дела|ты|вы|жизнь)|спасибо|благодарю|пока|до\s+свидания|"
    r"hi|hello|hey|good\s+(?:morning|evening|afternoon)|"
    r"how\s+are\s+you|thanks|thank\s+you|bye|goodbye)\b",
    re.IGNORECASE,
)
_SMALL_TALK_MAX_LEN = 60

# Приметы «программы» в тексте, который читает человек: секции внутреннего
# контракта, код-заборы, JSON-скобки в начале, трейсбек.
_TECHNICAL_MARKS_RE = re.compile(
    r"(^\s*(?:conclusion|facts|unverified|sources|confidence)\s*:|```|"
    r"^\s*[{\[]|traceback \(most recent call last\))",
    re.IGNORECASE | re.MULTILINE,
)
_HUMAN_REPLY_MAX_LEN = 350

_CYRILLIC_RE = re.compile(r"[а-яё]", re.IGNORECASE)

# Повтор: та же реплика, что уже уходила недавно, — НО в ответ на ДРУГОЙ
# вопрос. Урок старого прототипа (таймерный Telegram-спам назван провалом):
# слать одно и то же без спроса — шум. Граница второго экзаменатора
# (2026-08-28): человек, повторивший вопрос, заслуживает свой ответ снова —
# глушить его было бы ложным срабатыванием. Сравнение по множеству слов.
_REPEAT_JACCARD = 0.9
_SAME_QUESTION_JACCARD = 0.5


def _tokens(text: str) -> frozenset[str]:
    """Слова ≥3 знаков ПЛЮС полярные коротышки из канонного словаря.

    Находка второго экзаменатора 2026-08-28: правило длины молча резало «не»,
    и «тест прошёл»/«тест не прошёл» совпадали множествами — судья глушил
    именно изменение факта. Тот же класс уже чинился в topic_tokens; словарь
    ОДИН на репозиторий — второй завёл бы болезнь двух словарей.
    """
    from core.topic_tokens import _POLARITY_WORDS

    lowered = (text or "").lower()
    words = frozenset(re.findall(r"[a-zа-яё0-9]+", lowered))
    return frozenset(re.findall(r"[a-zа-яё0-9]{3,}", lowered)) | (
        words & _POLARITY_WORDS
    )


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def classify_register(incoming: str) -> str:
    """«small_talk» — короткая человеческая фраза без дела; иначе «substantive».

    Болтовня признаётся ВЫЧЕТОМ: после удаления формул вежливости не должно
    остаться ни одного содержательного слова. Иначе «спасибо, почему упал
    тест?» считалось бы болтовнёй по одной формуле благодарности — и деловой
    ответ был бы урезан (находка второго экзаменатора 2026-08-28).
    """
    text = (incoming or "").strip()
    if not text or len(text) > _SMALL_TALK_MAX_LEN:
        return "substantive"
    if not _SMALL_TALK_RE.search(text):
        return "substantive"
    remainder = _SMALL_TALK_RE.sub(" ", text)
    # Дело признаётся по ЛЮБОМУ содержательному знаку в остатке: «PR?»,
    # «C?», «γ?» — предмет разговора на любом письме (акронимная родня
    # MIR-007; все границы — находки Codex, 2026-08-28). Цена ошибки
    # асимметрична: дело-как-болтовня теряет улики ответа, болтовня-как-дело
    # лишь оставляет полный ответ — довод действует до конца, поэтому нет ни
    # порога длины, ни выбранного алфавита: содержательный знак — юникодная
    # буква или цифра. Утечка («ну привет» получит полный ответ) принята.
    if any(ch.isalnum() for ch in remainder):
        return "substantive"
    return "small_talk"


@dataclass(frozen=True)
class ReplyJudgement:
    """Вердикт судьи. ``verdict``: ok | trim | repeat_silence | silence.

    ``human_reply`` заполнен только при trim — механически укороченная
    человеческая форма (вывод без технических секций). ``reasons`` всегда
    называют, какое правило сработало; пустые reasons значат «ok».
    """

    verdict: str
    reasons: tuple[str, ...] = ()
    human_reply: str | None = None
    #: Заметки-наблюдения (язык не совпал и т.п.): в журнал, не в поведение —
    #: механической починки у них нет, чинить будет ступень 2 (LLM-переписка).
    observations: tuple[str, ...] = field(default=())


def _conclusion_only(reply: str) -> str | None:
    """Человеческое ядро ответа: строки Conclusion без остальных секций."""
    lines: list[str] = []
    in_conclusion = False
    for raw in (reply or "").splitlines():
        low = raw.strip().lower()
        if low.startswith("conclusion:"):
            in_conclusion = True
            rest = raw.strip()[len("conclusion:"):].strip()
            if rest:
                lines.append(rest)
            continue
        if re.match(r"^(facts|unverified|sources|confidence|safety)\s*:", low):
            in_conclusion = False
            continue
        if in_conclusion and raw.strip():
            lines.append(raw.strip())
    return " ".join(lines) or None


def judge_reply(
    incoming: str,
    reply: str,
    recent_exchanges: tuple[tuple[str, str], ...] = (),
) -> ReplyJudgement:
    """Судья пары «вопрос человека → ответ агента». Чистый и детерминированный.

    ``recent_exchanges`` — недавние пары (входящее, ответ): повтор ответа
    глушится только когда ВХОДЯЩЕЕ было другим; на повторённый вопрос
    повторённый ответ законен.
    """
    text = (reply or "").strip()
    if not text:
        return ReplyJudgement("silence", ("пустой ответ — молчание честнее",))

    reply_toks = _tokens(text)
    incoming_toks = _tokens(incoming)
    for old_incoming, old_reply in recent_exchanges:
        if (_jaccard(reply_toks, _tokens(old_reply)) >= _REPEAT_JACCARD
                and _jaccard(incoming_toks, _tokens(old_incoming))
                < _SAME_QUESTION_JACCARD):
            return ReplyJudgement(
                "repeat_silence",
                (
                    ("этот ответ уже уходил на другой вопрос — повтор без "
                     "спроса не разговор, а шум"),
                ),
            )

    observations: list[str] = []
    if _CYRILLIC_RE.search(incoming or "") and not _CYRILLIC_RE.search(text):
        observations.append("собеседник пишет по-русски, ответ без единой "
                            "русской буквы — регистр языка не услышан")

    if classify_register(incoming) == "small_talk" and (
        _TECHNICAL_MARKS_RE.search(text) or len(text) > _HUMAN_REPLY_MAX_LEN
    ):
        human = _conclusion_only(text)
        if human and len(human) <= _HUMAN_REPLY_MAX_LEN:
            return ReplyJudgement(
                "trim",
                ("на человеческую фразу — человеческий ответ, не отчёт",),
                human_reply=human,
                observations=tuple(observations),
            )
        return ReplyJudgement(
            "trim",
            (
                "на человеческую фразу — человеческий ответ, не отчёт",
                ("механически укоротить не вышло — ответ уходит как есть, "
                 "нарушение записано"),
            ),
            observations=tuple(observations),
        )

    if observations:
        return ReplyJudgement("ok", (), observations=tuple(observations))
    return ReplyJudgement("ok")

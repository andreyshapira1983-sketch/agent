"""Предупреждения проверки — человеческими словами у самого утверждения.

24.09, чат оператора: в каждом ответе стояли `[topic-only:tool:python_probe]`,
`[claim-figure-unverified]`, `[absence-unverifiable]` — метки для машины,
показанные человеку. Вычистить их нельзя: это предупреждения (граница
`_strip_verification_markers`, H-43), без них ответ увереннее, чем есть.

Как показывать неуверенность, измерено: Kim, Liao и др., «I'm Not Sure,
But...», FAccT 2024 (N=404) — высказанная словами неуверенность снижает
излишнее согласие с системой и повышает точность людей. Поэтому метка не
исчезает, а переводится: короткая оговорка на месте утверждения, источник
(адрес страницы) сохраняется. Делается только на краю показа
(`format_human_response`): в эпизоде и в разборе кампании метки остаются —
по ним считают и ищут источники.
"""
from __future__ import annotations

import re

# Оговорка от первого лица и коротко, как сказал бы человек (оператор 26.09).
_RU = {
    "topic-only": "не проверял",
    "claim-figure-unverified": "число не сверял",
    "absence-unverifiable": "что этого нет, проверить не могу",
    "claim-refuted": "но проверка говорит обратное",
    "улика-без-этих-слов": "в источнике этого нет",
    "dialogue-supported": "по нашему разговору",
    "subagent-asserted": "со слов помощника",
    "no-receipt": "не проверял",
    "unverified": "не проверял",
    "цитата-не-подтверждает": "не проверял",
}
_EN = {
    "topic-only": "unchecked",
    "claim-figure-unverified": "number unchecked",
    "absence-unverifiable": "can't verify it's absent",
    "claim-refuted": "but the check says otherwise",
    "улика-без-этих-слов": "the source doesn't say this",
    "dialogue-supported": "from our conversation",
    "subagent-asserted": "a helper's word",
    "no-receipt": "unchecked",
    "unverified": "unchecked",
    "цитата-не-подтверждает": "unchecked",
}
#: Виды меток-предупреждений: кто снимает метку со строки, обязан сказать это словами.
WARNING_KINDS: tuple[str, ...] = tuple(_RU)
_MARKER_RE = re.compile(
    r"\s*\[(topic-only|claim-figure-unverified|absence-unverifiable|claim-refuted|"
    r"улика-без-этих-слов|dialogue-supported|subagent-asserted|no-receipt|unverified|"
    r"цитата-не-подтверждает)(?::([^\]]*))?\]")
#: `[unverified:…]` ставится вместе с `[no-receipt]` и сам по себе ничего не добавляет.
_UNVERIFIED_BODY_RE = re.compile(r"\s*\[unverified:[^\]]*\]")
_URL_RE = re.compile(r"https?://\S+")
_CYRILLIC_RE = re.compile(r"[а-яё]", re.IGNORECASE)
_LATIN_RE = re.compile(r"[a-z]", re.IGNORECASE)


def _russian(text: str) -> bool:
    """Русский, если кириллицы хотя бы треть от латиницы: пути и имена кода в
    русском ответе латинские («Файл data/lesson_injections.jsonl не существует»)."""
    return 3 * len(_CYRILLIC_RE.findall(text)) >= len(_LATIN_RE.findall(text))


def humanize_warning_markers(text: str) -> str:
    """Метки-предупреждения -> короткая оговорка в скобках; адрес источника остаётся."""
    if "[" not in (text or ""):
        return text
    # Язык — по тексту БЕЗ меток: в короткой строке латиница самих меток
    # перевешивала русскую фразу.
    plain = _UNVERIFIED_BODY_RE.sub("", text)
    words = _RU if _russian(_MARKER_RE.sub("", plain)) else _EN
    seen_at_line: set[tuple[int, str]] = set()

    def say(match: re.Match[str]) -> str:
        kind, body = match.group(1), match.group(2) or ""
        line = plain.count("\n", 0, match.start())
        if (line, words[kind]) in seen_at_line:
            return ""
        seen_at_line.add((line, words[kind]))
        url = _URL_RE.search(body)
        source = f"{url.group(0).rstrip('.,;')} — " if url else ""
        return f" ({source}{words[kind]})"

    return _MARKER_RE.sub(say, plain)
